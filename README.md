# CloudBD CloudFormation Templates

CloudBD allows you to use your AWS S3 object storage as block storage (i.e., disks or volumes) for your Linux EC2 instances. CloudBD disks are up to 5x faster (2.5 GB/s), 1000x more durable, and 90% lower cost (thinly allocated/usage-based) than native AWS EBS volumes.

The easiest way to try out CloudBD disks on AWS is by using CloudFormation and the [CloudBD All-In-One template](https://github.com/dev-cloudbd/cfn-templates/blob/master/cloudbd-aio.yml). This template creates an isolated VPC environment and an EC2 instance (Ubuntu 18.04LTS/Bionic) with a CloudBD disk attached for testing. Once created, ssh to the instance and try out the CloudBD disk. When finished, simply delete the CloudFormation stack to clean up all CloudBD resources.

The [CloudBD S3 Remote template](https://github.com/dev-cloudbd/cfn-templates/blob/master/remote.yml) lets you create CloudBD disks directly from CloudFormation templates using a custom resource lambda. The full documentation for using CloudBD disks with CloudFormation is available at the [CloudBD Documentation pages](https://www.cloudbd.io/docs/remote-aws-s3.html#cloudformation-setup-guide). Complete example templates for all supported Linux distros are provided in this git repository.

## CloudBD All-In-One

### **Prerequisites**

1. An AWS account with the necessary permissions to create a CloudBD All-In-One CloudFormation stack.
2. An existing EC2 key pair in your AWS region and the general knowledge of how to use an AWS key pair to ssh to an EC2 instance.

**Please note that while signing up for and using the CloudBD trial tier is free, any AWS charges (e.g., S3 storage hours, S3 requests, EC2 instance time) will still apply.**

### **Setup**

1. **Sign up for a free CloudBD account at [manage.cloudbd.io/signup](https://manage.cloudbd.io/signup)**

   Signup is quick and easy and only requires a valid email address. Once you've signed up, follow the directions to verify your email address and then login to the CloudBD Management dashboard.

2. **Get a copy of your CloudBD credentials.json**

   Your CloudBD credentials.json file acts as a license key for your CloudBD account. This file is required to create CloudBD disks or attach them to a server.

   Your CloudBD credentials.json file can be downloaded from the [CloudBD Management Dashboard - Credentials page](https://manage.cloudbd.io/credentials). Press the **Get Credentials** button to save a copy of your credentials.json file to your Downloads directory.

3. **Upload your CloudBD credentials.json to your AWS SSM Parameter Store**

   The CloudBD All-In-One requires a copy of your credentials.json stored in an AWS SSM parameter. Storing your credentials in an encrypted SSM parameter is a secure and easy way to automate deployment of the credentials.json file to your EC2 instances.

   **Steps:**

   1. In the AWS Management Console, go to Services -> Systems Manager -> Parameter Store
   2. Select **Create parameter**
   3. Enter Parameter details:

      **Name:** /cloudbd/credentials.json

      **Description:** CloudBD Credentials

      **Tier:** Standard

      **Type:** SecureString

      **KMS Key ID:** Select either the default AWS key 'alias/aws/ssm' or select a customer managed KMS key

      **Value:** Copy the data from your downloaded credentials.json file here

   4. Select **Create parameter**

4. **Create a CloudBD All-In-One stack using AWS CloudFormation**

   **Template Summary:**

   * Creates an S3 bucket for storing your CloudBD disk data
   * Creates an IAM role for reading your CloudBD credentials and accessing your CloudBD S3 bucket
   * Creates a CloudBD disk in the CloudBD S3 bucket
   * Creates a single subnet VPC with an internet gateway and the S3 gateway VPC endpoint
   * Creates an Ubuntu 18.04LTS/Bionic EC2 instance in the VPC with the CloudBD IAM role applied.
   * Attaches the CloudBD disk to the EC2 instance, formats the disk with an Ext4 filesystem, and mounts the disk at **/mnt**

   **Steps:**

   1. In the AWS Management Console, go to Services -> CloudFormation
   2. Select **Create stack**
   3. Select **Template is ready** and choose one of the following:

      * Select **Amazon S3 URL** and enter the following URL:

        https://s3.amazonaws.com/cfn-templates.cloudbd.io/cloudbd-aio.yml

      * Download a local copy of the template from the [CloudBD CloudFormation GitHub project](https://github.com/dev-cloudbd/cfn-templates/blob/master/cloudbd-aio.yml). Then select **Upload a template file** and choose your local copy.

   4. Select **Next**
   5. Specify stack details:

      **Stack name:** Enter a name for your CloudBD All-In-One stack

      **CloudBD S3 Remote Parameters:**

      * SSM Parameter Store Region: Choose the AWS region where you created the CloudBD credentials.json SSM parameter
      * SSM Parameter Name: Enter the name of the SSM parameter that contains your CloudBD credentials.json
      * Customer KMS Key: If your credentials.json SSM parameter uses the default AWS key 'alias/aws/ssm', leave this parameter empty. Otherwise, enter the KMS Key ID (actual ID, not an alias) used to encrypt your SSM parameter.
      * Server-Side Encryption: Enable or disable server-side encryption for the S3 remote bucket that stores the CloudBD disk data
      * HTTP Protocol: Choose wheather CloudBD disks should use HTTP or HTTPS when communicating with the S3 bucket

      **VPC/EC2 Instance Parameters:**

      * Availability Zone: Choose the availability zone for the VPC subnet and EC2 instance
      * EC2 Instance Type: Choose the instance type
      * EC2 Key Pair: Choose an EC2 key pair that can be used to ssh to the instance
      * SSH Location (Optional): Restrict the allowed IP range that can ssh to the instance

    6. Select **Next**
    7. Configure stack options (Optional): Nothing is required on this page but you can optionally add tags, restrict the CloudFormation permissions to an IAM role, and configure other stack policies here.
    8. Select **Next**
    9. Review Stack: Acknowledge **Capabilities**

        The CloudBD All-In-One template creates an IAM Policy that allows:

        * Read and write access to the CloudBD S3 bucket
        * Read access to the CloudBD credentials.json SSM parameter

        This policy is attached to:

        * The CloudBD disk lambda role to allow it to create your CloudBD disks directly from CloudFormation templates as a custom resource type.
        * The EC2 instance role to allow it to attach and use CloudBD disks

        Select the checkbox to acknowledge the CloudFormation capabilities.

    11. Select **Create stack**

        The CloudBD All-In-One typically takes between 4 or 5 minutes for CloudFormation to complete.

### **Testing**

1. **Ssh to the Instance**

   In order to ssh to the instance you will need the EC2 key pair **.pem** file for the key chosen during stack creation. This file is automatically downloaded when you create an EC2 key pair.

   Additionally, you will need the public IP address for the EC2 instance. In the AWS management console, navigate to **Services -> EC2** and select **Running Instances**. Then, select your CloudBD All-In-One instance. The public IP address and DNS name is available in the description.

   From a command prompt:

   ```bash
   ssh -i </PATH/TO/YOUR/KEY_PAIR.pem> ubuntu@<INSTANCE_PUBLIC_IP_OR_DNS_NAME>
   ```

   Once connected to the instance you can find your CloudBD disk in the **/dev/mapper/** directory. An Ext4 filesystem has already been created on the CloudBD disk and mounted at **/mnt/**.

2. **Fio Tests**

   The All-In-One instance comes preinstalled with [Fio](https://fio.readthedocs.io/en/latest/index.html), a high performance filesystem and block device testing utility. Several fio scripts for common read and write patterns are provided in the ubuntu user's home directory.

   Example running an fio test:

   ``` bash
   fio /home/ubuntu/write.fio
   ```

### **Cleanup**

When finished with the CloudBD All-In-One you can clean up all CloudBD resources by deleting the All-In-One stack from CloudFormation. In the AWS Management Console, navigate to **Services -> CloudFormation** and select your CloudBD All-In-One stack. Then select **Delete** and confirm.

**Please note that cleaning up the All-In-One stack will also delete any data on the CloudBD disk.**
