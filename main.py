import streamlit as st
import boto3
import subprocess
import base64
from botocore.exceptions import NoCredentialsError, ClientError

# --- Helper Functions ---

def get_ecr_client(region_name):
    """
    Creates and returns a boto3 ECR client using default credentials.

    Args:
        region_name (str): The AWS region to connect to.

    Returns:
        boto3.client or None: An ECR client if successful, otherwise None.
    """
    try:
        # boto3 will automatically search for credentials in the standard
        # locations (environment variables, ~/.aws/credentials)
        return boto3.client('ecr', region_name=region_name)
    except NoCredentialsError:
        st.error("AWS credentials not found. Please configure them using 'aws configure' or environment variables.")
        return None
    except Exception as e:
        st.error(f"Error creating ECR client: {e}")
        return None

def get_ecr_repositories(ecr_client):
    """
    Fetches a list of ECR repository names.

    Args:
        ecr_client (boto3.client): The ECR client.

    Returns:
        list or None: A list of repository names if successful, otherwise None.
    """
    try:
        paginator = ecr_client.get_paginator('describe_repositories')
        repositories = []
        for page in paginator.paginate():
            for repo in page['repositories']:
                repositories.append(repo['repositoryName'])
        return repositories
    except ClientError as e:
        st.error(f"Error fetching ECR repositories: {e}. Check your credentials and permissions.")
        return None
    except Exception as e:
        st.error(f"An unexpected error occurred: {e}")
        return None


def ecr_login(ecr_client, aws_account_id, region):
    """
    Logs Docker into the Amazon ECR registry.

    Args:
        ecr_client (boto3.client): The ECR client.
        aws_account_id (str): Your AWS account ID.
        region (str): The AWS region of the ECR registry.

    Returns:
        bool: True if login is successful, False otherwise.
    """
    st.info("Authenticating Docker with ECR...")
    try:
        auth_token_response = ecr_client.get_authorization_token()
        auth_data = auth_token_response['authorizationData'][0]
        token = auth_data['authorizationToken']
        decoded_token = base64.b64decode(token).decode('utf-8')
        username, password = decoded_token.split(':')
        registry = f"{aws_account_id}.dkr.ecr.{region}.amazonaws.com"

        # Using subprocess to run the docker login command
        command = [
            "docker", "login",
            "--username", username,
            "--password", password,
            registry
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)

        if result.returncode == 0:
            st.success("Docker login successful!")
            return True
        else:
            # This part might not be reached due to check=True, but is good for safety
            st.error("Docker login failed.")
            st.code(result.stderr)
            return False

    except subprocess.CalledProcessError as e:
        st.error("Docker login failed.")
        st.code(e.stderr)
        return False
    except Exception as e:
        st.error(f"An error occurred during ECR login: {e}")
        return False

def tag_and_push_image(local_image_tag, repository_uri, image_tag):
    """
    Tags a local Docker image and pushes it to the specified ECR repository.

    Args:
        local_image_tag (str): The tag of the local Docker image.
        repository_uri (str): The URI of the target ECR repository.
        image_tag (str): The tag to apply to the image in the repository.

    Returns:
        bool: True if the push is successful, False otherwise.
    """
    ecr_image_uri = f"{repository_uri}:{image_tag}"
    st.info(f"Tagging local image '{local_image_tag}' as '{ecr_image_uri}'...")

    # Tagging command
    try:
        tag_command = ["docker", "tag", local_image_tag, ecr_image_uri]
        subprocess.run(tag_command, capture_output=True, text=True, check=True)
        st.success("Image tagged successfully.")
    except subprocess.CalledProcessError as e:
        st.error("Failed to tag Docker image. Make sure the local image exists.")
        st.code(e.stderr)
        return False

    # Pushing command
    st.info(f"Pushing image to ECR repository: {ecr_image_uri}")
    push_command = ["docker", "push", ecr_image_uri]
    
    with st.spinner('Pushing Docker image... This may take a while.'):
        process = subprocess.Popen(push_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
        
        log_container = st.empty()
        logs = ""
        for line in iter(process.stdout.readline, ''):
            logs += line
            log_container.code(logs, language='bash')
        process.wait()

    if process.returncode == 0:
        st.success("Image pushed successfully to ECR!")
        return True
    else:
        st.error("Failed to push image to ECR.")
        return False

# --- Streamlit App UI ---

st.set_page_config(page_title="AWS ECR Docker Pusher", layout="wide")

st.title("🚢 AWS ECR Docker Image Pusher")
st.markdown("""
This application helps you push a local Docker image to an Amazon Elastic Container Registry (ECR) repository.
It uses your default AWS credentials, which you can set up with the AWS CLI.
""")

# --- AWS Configuration ---
st.header("1. AWS Configuration")

st.info("""
**Prerequisite:** Please configure your AWS credentials before using this app.
The easiest way is with the AWS CLI:
1. Install the AWS CLI.
2. Run `aws configure` in your terminal and enter your Access Key ID, Secret Access Key, and default region.
""")

aws_region = st.text_input("AWS Region", help="e.g., us-east-1")
aws_account_id = st.text_input("AWS Account ID", help="The 12-digit AWS account number.")

if st.button("Connect to AWS"):
    region = aws_region.strip()

    if all([region, aws_account_id.strip()]):
        st.session_state.ecr_client = get_ecr_client(region)
        if st.session_state.ecr_client:
            with st.spinner("Fetching ECR repositories..."):
                st.session_state.repos = get_ecr_repositories(st.session_state.ecr_client)
            if st.session_state.repos is not None:
                st.success("Successfully connected to AWS and fetched repositories.")
    else:
        st.warning("Please provide your AWS Region and Account ID.")


# --- ECR Repository and Image Details ---
if 'repos' in st.session_state and st.session_state.repos is not None:
    if not st.session_state.repos:
        st.warning("No ECR repositories found in this region. Please create one in the AWS console.")
    else:
        st.header("2. Select Repository and Image")
        selected_repo = st.selectbox("Choose an ECR Repository", st.session_state.repos)
        local_image = st.text_input("Local Docker Image Name (e.g., my-app:latest)", "my-app:latest")
        new_image_tag = st.text_input("Tag for ECR Image", "latest")
        
        account_id = aws_account_id.strip()
        region = aws_region.strip()

        if st.button("Push to ECR"):
            if all([selected_repo, local_image, new_image_tag, account_id]):
                login_success = ecr_login(st.session_state.ecr_client, account_id, region)

                if login_success:
                    repository_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{selected_repo}"
                    tag_and_push_image(local_image.strip(), repository_uri, new_image_tag.strip())
            else:
                st.warning("Please fill in all the repository and image details.")

st.sidebar.header("How to use")
st.sidebar.markdown("""
1.  **Configure AWS CLI (One-time setup)**: Open your terminal and run `aws configure`. Enter your AWS Access Key ID, Secret Access Key, and default region.
2.  **Enter Region & Account ID**: Provide your AWS Region and 12-digit Account ID in the app.
3.  **Connect**: Click "Connect to AWS". The app will use your configured credentials to find your ECR repositories.
4.  **Select Repository**: Choose the target ECR repository.
5.  **Specify Image**: Enter the name and tag of your local Docker image.
6.  **Set ECR Tag**: Provide a tag for the image in ECR.
7.  **Push**: Click "Push to ECR" to start the process.
""")
st.sidebar.warning("This app uses your default AWS credentials file and does not handle or store them directly.")
