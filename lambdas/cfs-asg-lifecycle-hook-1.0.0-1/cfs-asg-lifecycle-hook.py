import boto3
from botocore.exceptions import WaiterError
import json
import logging
import time
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)
ssm_client = boto3.client("ssm")
route53_client = boto3.client('route53')
asg_client = boto3.client('autoscaling')
ec2_client = boto3.client('ec2')

DOCUMENT_NAME = os.environ.get("LIFECYCLE_HOOK_DOCUMENT_NAME", "") # Required
LIFECYCLE_HOOK = os.environ.get("LIFECYCLE_HOOK_NAME", "") # Required
HOSTED_ZONE = os.environ.get("ROUTE53_HOSTED_ZONE", "") # Required

# autoscaling lifecycle hook terminate event keys
ASG_KEY = "AutoScalingGroupName"
EC2_KEY = "EC2InstanceId"
RESPONSE_DOCUMENT_KEY = "DocumentIdentifiers"

# ssm run command invocation change event keys
INSTANCE_ID_KEY = "instance-id"
STATUS_KEY = "status"
RESPONSE_TAGS_KEY = "Tags"
FAILED_STATUS = "Failed"
TIMED_OUT_STATUS = "TimedOut"

# autoscaling lifecycle hook startup event keys
CLUSTER_KEY = "ClusterName"
NODE_ID_KEY = "NodeId"
NODE_IP_KEY = "NodeIp"
INSTANCE_ID_KEY = "InstanceId"

def check_response(response_json):
    try:
        if response_json['ResponseMetadata']['HTTPStatusCode'] == 200:
            return True
        else:
            return False
    except KeyError:
        return False

def list_document():
    document_filter_parameters = {'key': 'Name', 'value': DOCUMENT_NAME}
    response = ssm_client.list_documents(
        DocumentFilterList=[ document_filter_parameters ]
    )
    return response

def check_document():
    # If the document already exists, it will not create it.
    try:
        response = list_document()
        if check_response(response):
            logger.info("Documents list: %s", response)
            if response[RESPONSE_DOCUMENT_KEY]:
                logger.info("Documents exists: %s", response)
                return True
            else:
                return False
        else:
            logger.error("Documents' list error: %s", response)
            return False
    except Exception as e:
        logger.error("Document error: %s", str(e))
        return None

def send_command(instance_id, params={}):
    # Until the document is not ready, waits in accordance to a backoff mechanism.
    while True:
        timewait = 1
        response = list_document()
        if any(response[RESPONSE_DOCUMENT_KEY]):
            break
        time.sleep(timewait)
        timewait += timewait
    try:
        response = ssm_client.send_command(
            InstanceIds = [ instance_id ],
            DocumentName = DOCUMENT_NAME,
            TimeoutSeconds = 120,
            Parameters = params
            )
        if check_response(response):
            logger.info("Command sent: %s", response)
            return response['Command']['CommandId']
        else:
            logger.error("Command could not be sent: %s", response)
            return None
    except Exception as e:
        logger.error("Command could not be sent: %s", str(e))
        return None

def check_command(command_id, instance_id):
    timewait = 1
    while True:
        response_iterator = ssm_client.list_command_invocations(
            CommandId = command_id,
            InstanceId = instance_id,
            Details=False
            )
        if check_response(response_iterator):
            if response_iterator['CommandInvocations']:
              response_iterator_status = response_iterator['CommandInvocations'][0]['Status']
              if response_iterator_status != 'Pending':
                  if response_iterator_status == 'InProgress' or response_iterator_status == 'Success':
                      logging.info( "Status: %s", response_iterator_status)
                      return True
                  else:
                      logging.error("ERROR: status: %s", response_iterator)
                      return False
        time.sleep(timewait)
        timewait += timewait

def abandon_lifecycle(instance_id, auto_scaling_group = None):
    try:
        if not auto_scaling_group:
            response = ec2_client.describe_tags(
                Filters=[
                    {
                        'Name': 'resource-id',
                        'Values': [instance_id]
                    },
                    {
                        'Name': 'key',
                        'Values': ['aws:autoscaling:groupName']
                    }
                    ]
                )
            if check_response(response):
                if response[RESPONSE_TAGS_KEY]:
                    auto_scaling_group = response[RESPONSE_TAGS_KEY][0]['Value']
                    logger.info("Found auto scaling group name from instance tags")
                else:
                    logger.error('Lifecycle hook could not be abandoned: missing auto scaling group name instance tag')
                    return None
            else:
                logger.error('Lifecycle hook could not be abandoned: unable to query instance tags for auto scaling group name')
                return None

        response = asg_client.complete_lifecycle_action(
                LifecycleHookName=LIFECYCLE_HOOK,
                AutoScalingGroupName=auto_scaling_group,
                LifecycleActionResult='ABANDON',
                InstanceId=instance_id
                )
        if check_response(response):
            logger.info("Lifecycle hook abandoned correctly: %s", response)
        else:
            logger.error("Lifecycle hook could not be abandoned: %s", response)
    except Exception as e:
        logger.error("Lifecycle hook abandon could not be executed: %s", str(e))
        return None

def update_node_dns(name, ip, instance_id):
    try:
        response = route53_client.change_resource_record_sets(
                HostedZoneId=HOSTED_ZONE,
                ChangeBatch={
                    'Changes': [
                        {
                            'Action': 'UPSERT',
                            'ResourceRecordSet': {
                                'Name': name,
                                'Type': 'A',
                                'TTL': 10,
                                'ResourceRecords': [ { 'Value': ip } ]
                            }
                        }
                    ]
                }
                )

        if check_response(response):
            logger.info("Node DNS route change requested: %s", response)
            change_id = response['ChangeInfo']['Id']
            dns_update_waiter = route53_client.get_waiter('resource_record_sets_changed')
            # wait up to 5 minutes for dns propogation to sync
            dns_update_waiter.wait(Id=change_id, WaiterConfig={'Delay': 5, 'MaxAttempts': 60})
            ec2_client.create_tags(
                    Resources=[ instance_id ],
                    Tags=[
                        {
                            'Key': 'CFSNodeDns',
                            'Value': name
                        }
                    ]
                    )
        else:
            logger.error("Failed to request node DNS route change: %s", response)
    except Exception as e:
        logger.error("Update node dns could not be executed: %s", str(e))

def check_environment():
    if not LIFECYCLE_HOOK:
        logger.error("Missing required envrionment variable 'LIFECYCLE_HOOK_NAME'")
        return False

    if not DOCUMENT_NAME:
        logger.error("Missing required environment variable 'LIFECYCLE_HOOK_DOCUMENT_NAME'")
        return False

    if not HOSTED_ZONE:
        logger.error("Missing required environment variable 'CFS_HOSTED_ZONE'")
        return False
    return True

def handler(event, context):
    try:
        logger.info(json.dumps(event))
        source = event['source']
        message = event['detail']
        auto_scaling_group = None
        force = 'no'

        if not check_environment():
            logging.error("Missing required environment variables")

        # Startup lifecycle hook
        # Instance finds an available node id using tags and then emits a cloudwatch event for this lambda
        # to handle the update to the route53 dns. Private subnets can't directly update route53 yet
        # because there is no route53 vpc endpoint. In the future, if one exists, this logic can more
        # cleanly be implemented directly in the network startup scripts instead of a lambda. In this
        # implementation we update the route53 record to the instance's ip and set a tag on the instance
        # so that the startup scripts know when the dns update has fully propogated and it is safe to start
        # glusterd.
        if source == 'cfs.autoscaling':
            if (CLUSTER_KEY in message and
                    NODE_ID_KEY in message and
                    NODE_IP_KEY in message and
                    INSTANCE_ID_KEY in message):
                cluster_name = message[CLUSTER_KEY]
                node_id = message[NODE_ID_KEY]
                node_ip = message[NODE_IP_KEY]
                instance_id = message[INSTANCE_ID_KEY]

                update_node_dns(
                        'node' + node_id + '.' + cluster_name + '.cfs.cloudbd.io',
                        node_ip,
                        instance_id
                        )
                logging.info("Updated route53 DNS entries for new node")
            else:
                logging.error("Missing required cfs.autoscaling parameters in event details")
            return None

        # Terminate lifecycle hook
        # When an instance is terminated due to an autoscaling event we attempt to cleanly shutdown the
        # server by invoking graceful stop commands of gluster. In the event that we fail to execute
        # the commands, ssm will attempt to reexecute the terminate scripts with a force flag set if a
        # timeout was reached. Otherwise, the lambda will complete the lifecycle hook allowing the instance
        # to be terminated by the ASG.
        if source == 'aws.autoscaling' and EC2_KEY in message and ASG_KEY in message:
            instance_id = message[EC2_KEY]
            auto_scaling_group = message[ASG_KEY]
        elif source == 'aws.ssm' and INSTANCE_ID_KEY in message and STATUS_KEY in message:
            instance_id = message[INSTANCE_ID_KEY]
            status = message[STATUS_KEY]

            if status == TIMED_OUT_STATUS:
                force = 'yes'
            elif status == FAILED_STATUS:
                abandon_lifecycle(instance_id)
                return None
            else:
                logging.error("Unrecognized SSM event status")
                return None
        else:
            parsed_message = json.dumps(message)
            logging.error("No valid JSON message: %s", parsed_message)
            return None

        # Invoke SSM document for terminate lifecycle hook
        if not check_document():
            logging.error("Document not found")
            abandon_lifecycle(instance_id, auto_scaling_group)

        command_id = send_command(instance_id, params={ 'force': [force] })
        if command_id != None:
            if check_command(command_id, instance_id):
                logging.info("Command started successfully")
            else:
                logging.error("Command failed to start")
                abandon_lifecycle(instance_id, auto_scaling_group)
        else:
            logging.error("Command failed to send")
            abandon_lifecycle(instance_id, auto_scaling_group)
    except Exception as e:
        logging.error("Error: %s", str(e))
