import logging
import os
import json

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
import smtplib

import azure.functions as func
from azure.storage.fileshare import ShareFileClient
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential


def get_param(request, param_name):
    """Extract parameter from incoming request."""
    param = request.params.get(param_name)
    if not param:
        try:
            req_body = request.get_json()
        except ValueError:
            param = None
        else:
            param = req_body.get(param_name)

    if not param:
        return None
    else:
        return param


class SenderDB:
    def __init__(self, conn_str, share_name, file_path):
        file_client = ShareFileClient.from_connection_string(
            conn_str=conn_str,
            share_name=share_name,
            file_path=file_path,
        )

        with open("temp_emails.json", "wb") as file_handle:
            data = file_client.download_file()
            data.readinto(file_handle)

        with open("temp_emails.json", "r") as file_handle:
            data = file_handle.read()
            logging.info(data)
            self.email_db = json.loads(data)

    def get_sender(self, user):
        sender_details = [x for x in self.email_db if x["user"] == user]
        if len(sender_details) == 0:
            logging.info("Sender user not found in DB.")
            raise KeyError("Sender not found in DB.")
        elif len(sender_details) > 1:
            logging.info("More than one sender user in DB. Please fix.")
            raise KeyError("Ambiguous sender found in DB")
        else:
            sender_details = sender_details[0]

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=os.environ["KEY_VAULT_URI"], credential=credential)
        secret = client.get_secret(sender_details["keyvault_secret"])
        sender_details["password"] = secret.value

        return sender_details


def parse_request(req):
    param_names = ["user", "subject", "recipients", "body"]
    email_parameters = {k: get_param(req, k) for k in param_names}
    logging.info(f"The incoming parameters are: {email_parameters}")

    # Further prep the parameters
    if not email_parameters["recipients"]:
        logging.info("Failed delivery. No recipients received.")
        raise KeyError("Email recipients not specified in request.")
    else:
        email_parameters["recipients"] = email_parameters["recipients"].split(",")

    if not email_parameters["user"]:
        logging.info("Failed delivery. No sender user specified.")
        raise KeyError("Sender user was not specified in request.")

    if not email_parameters["body"]:
        email_parameters["body"] = ""
    if not email_parameters["subject"]:
        email_parameters["subject"] = ""

    return email_parameters


class EmailDeliverer:
    def __init__(self, host, port, email, password):
        self.host = host
        self.port = port
        self.password = password
        self.email = email

    def send_email(self, recipients, subject, body):
        msg = MIMEMultipart()
        msg["From"] = self.email
        msg["To"] = ",".join(recipients)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg.attach(MIMEText(body))

        server = smtplib.SMTP(self.host, self.port)
        server.starttls()
        server.login(self.email, self.password)
        server.send_message(msg)
        server.quit()


def main(req):
    """Azure function to send emails triggered by HTTP request."""
    logging.info("Send email triggered via HTTP.")

    email_parameters = parse_request(req)

    sender_details = SenderDB(
        conn_str=os.environ["AzureWebJobsStorage"],
        share_name="email-app",
        file_path="emails.json",
    ).get_sender(email_parameters["user"])

    postman = EmailDeliverer(
        host=sender_details["host"],
        port=sender_details["port"],
        email=sender_details["email"],
        password=sender_details["password"],
    )

    postman.send_email(
        recipients=email_parameters["recipients"],
        subject=email_parameters["subject"],
        body=email_parameters["body"],
    )

    return func.HttpResponse("{}")
