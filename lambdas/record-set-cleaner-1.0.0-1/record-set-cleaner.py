# IAM Permissions
#   Requires
#   - 'route53:ListResourceRecordSets'
#   - 'route53:ChangeResourceRecordSets'
#   - 'route53:GetChange'
#   Optional
#   - 'logs:CreateLogGroup'
#   - 'logs:CreateLogStream'
#   - 'logs:PutLogEvents'
import logging
import os
import time
import boto3
from crhelper import CfnResource

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
helper = CfnResource()

try:
    route53_client = boto3.client('route53')
except Exception as e:
    helper.init_failure(e)

@helper.create
@helper.update
def no_op(_, __):
    pass

def check_response(response):
    try:
        if response['ResponseMetadata']['HTTPStatusCode'] == 200:
            return True
        else:
            return False
    except KeyError:
        return False

def wait_for_dns_change_completion(response):
    timewait = 1
    while check_response(response) and response['ChangeInfo']['Status'] == 'PENDING':
        time.sleep(timewait)
        timewait += timewait
        changeId = response['ChangeInfo']['Id']
        response = route53_client.get_change(Id=changeId)
        LOGGER.info('Get change: %s', response)

    if check_response(response) and response[CHANGE_INFO_KEY]['Status'] == 'INSYNC':
        LOGGER.info('Delete DNS records completed successfully.')
    else:
        LOGGER.info('Delete DNS records failed.')

@helper.delete
def delete_dns_records(event, __):
    zoneId = event['ResourceProperties']['ZoneId']
    recordNames = event['ResourceProperties']['RecordNames'].split(sep=',')
    recordSetPaginator = route53_client.get_paginator('list_resource_record_sets')
    recordSetIterable = recordSetPaginator.paginate(HostedZoneId=zoneId)

    changes = [
        {
            'Action': 'DELETE',
            'ResourceRecordSet': record
        }
        for recordSet in recordSetIterable
            for record in recordSet['ResourceRecordSets']
                if record['Name'] in recordNames
    ]

    if changes:
        response = route53_client.change_resource_record_sets(
                HostedZoneId=zoneId,
                ChangeBatch={ 'Changes': changes }
            )
        LOGGER.info('Change resource record set: %s', response)
        wait_for_dns_change_completion(response)
    else:
        LOGGER.info('No matching DNS records found.')

def handler(event, context):
    helper(event, context)
