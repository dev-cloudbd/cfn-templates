import logging
import signal
import os
import json
import subprocess
import re
import boto3
from botocore.vendored import requests

os.environ['PATH'] = os.environ['PATH'] + ":" + os.environ.get('LAMBDA_TASK_ROOT', '.') + '/bin'

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

SUCCESS = "SUCCESS"
FAILED = "FAILED"

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_DISK_NOT_FOUND = 2

def send(event, context, responseStatus, responseData=None, physicalResourceId=None, noEcho=False, responseReason=None):
  responseUrl = event['ResponseURL']
  if responseReason:
    responseReason += ' '
  else:
    responseReason = ''
  responseReason += 'See the details in CloudWatch Log Stream: ' + context.log_stream_name

  responseBody = {}
  responseBody['Status'] = responseStatus
  responseBody['Reason'] = responseReason
  responseBody['PhysicalResourceId'] = physicalResourceId or context.log_stream_name
  responseBody['StackId'] = event['StackId']
  responseBody['RequestId'] = event['RequestId']
  responseBody['LogicalResourceId'] = event['LogicalResourceId']
  responseBody['NoEcho'] = noEcho
  if responseData:
    responseBody['Data'] = responseData

  json_responseBody = json.dumps(responseBody, separators=(",",":"))
  LOGGER.info("Response body:\n" + json_responseBody)

  headers = {
    'content-type' : '',
    'content-length' : str(len(json_responseBody))
  }

  try:
    response = requests.put(responseUrl, data=json_responseBody, headers=headers)
    LOGGER.info("Status code: " + response.reason)
  except Exception as e:
    LOGGER.info("send(..) failed executing requests.put(..): " + str(e))

class TimeoutError(Exception):
  pass

class CredentialsError(Exception):
  pass

def timeout_handler(_signal, _frame):
  raise TimeoutError('Time limit exceeded')

signal.signal(signal.SIGALRM, timeout_handler)
creds_path = "/tmp/cloudbd-credentials.json"

def get_credentials(ssm_region, ssm_parameter):
  try:
    if not os.path.isfile(creds_path):
      ssm = boto3.client('ssm', region_name=ssm_region)
      creds = ssm.get_parameter(Name=ssm_parameter, WithDecryption=True)['Parameter']['Value']
      with open(creds_path, "w") as creds_file:
        creds_file.write(creds)
  except Exception as e:
    LOGGER.info(str(e))
    raise CredentialsError("unable to download CloudBD credentials from SSM paramater store")

def create_disk(remote, disk, size):
  subprocess.check_call(["cloudbd", "create", "--creds=" + creds_path, "--remote=" + remote, "--disk=" + disk, "--size=" + size])

def delete_disk(remote, disk):
  subprocess.check_call(["cloudbd", "delete", "--creds=" + creds_path, "--remote=" + remote, "--disk=" + disk])
  
def get_disk_info(remote, disk):
  output = subprocess.check_output(["cloudbd", "info", "--remote=" + remote, "--disk=" + disk, "-e"])
  return dict(re.findall(r'(\S+)\s*=\s*(".*?"|\S+)', output))

def handler(event, context):
  physicalResourceId = "0"
  signal.alarm((context.get_remaining_time_in_millis() / 1000) - 1)
  LOGGER.info('Request received event:\n%s', json.dumps(event))

  try:
    requestType = event['RequestType']

    if requestType == 'Update':
      LOGGER.info("Request failed: cannot update a CloudBD disk")
      send(event, context, FAILED, None, physicalResourceId, None, "Cannot change CloudBD disk properties")
      return
    elif requestType == 'Delete':
      physicalResourceId = event['PhysicalResourceId']
      if physicalResourceId == "0":
        send(event, context, SUCCESS, None, physicalResourceId)
        return

    get_credentials(os.environ['CLOUDBD_CREDS_SSM_REGION'], os.environ['CLOUDBD_CREDS_SSM_PARAM'])

    remote_dict = {
      "type": "aws_iam_temp",
      "region": os.environ['AWS_REGION'],
      "bucket": os.environ['CLOUDBD_REMOTE_BUCKET'],
      "protocol": "https",
      "access_key_id": os.environ['AWS_ACCESS_KEY_ID'],
      "secret_access_key": os.environ['AWS_SECRET_ACCESS_KEY'],
      "session_token": os.environ['AWS_SESSION_TOKEN']
    }
    remote = 'data:application/json,' + json.dumps(remote_dict, separators=(",",":"))
    disk = event['ResourceProperties']['Name']

    if requestType == 'Create':
      size = event['ResourceProperties']['Size']
      LOGGER.info("Request cloudbd create disk '%s' of size '%s'", disk, size)
      create_disk(remote, disk, size)
      diskinfo = get_disk_info(remote, disk)
      physicalResourceId = "cbd-" + diskinfo['CBD_UUID']
      send(event, context, SUCCESS,
        {
          'Name': diskinfo['CBD_DEVICE'],
          'Size': diskinfo['CBD_SIZE'],
          'Uuid': physicalResourceId
        },
        physicalResourceId)
    elif requestType == 'Delete':
      physicalResourceId = event['PhysicalResourceId']
      LOGGER.info("Request cloudbd destroy disk '%s (%s)'", disk, physicalResourceId)
      try:
        diskinfo = get_disk_info(remote, disk)
        if "cbd-" + diskinfo['CBD_UUID'] == physicalResourceId:
          delete_disk(remote, disk)
        else:
          LOGGER.info("Disk UUID mismatch, non-CloudFormation managed resource exists... skipping delete")
        send(event, context, SUCCESS, None, physicalResourceId)
      except subprocess.CalledProcessError as e:
        if e.returncode == EXIT_DISK_NOT_FOUND:
          LOGGER.info("Disk not found, assuming already deleted")
          send(event, context, SUCCESS, None, physicalResourceId)
        else:
          LOGGER.info("Failed to delete disk '%s': %s", disk, e.output)
          send(event, context, FAILED, None, physicalResourceId)
    else:
      LOGGER.info("Request failed: unexpected event type '%s'", event['RequestType'])
      send(event, context, FAILED, None, physicalResourceId)
  except TimeoutError:
    LOGGER.info("Request failed: time limit exceeded")
    send(event, context, FAILED, None, physicalResourceId, None, "Time limit exceeded.")
  except CredentialsError:
    LOGGER.info("Request failed: unable to get CloudBD credentials from SSM parameter")
    send(event, context, FAILED, None, physicalResourceId, None, "Failed to get CloudBD credentials from SSM parameter.")
  except Exception as e:
    LOGGER.info("Request failed: %s", repr(e))
    send(event, context, FAILED, None, physicalResourceId)
  finally:
    signal.alarm(0)

