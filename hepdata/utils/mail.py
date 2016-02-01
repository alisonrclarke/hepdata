from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP

from celery import shared_task
from flask import current_app
from flask.ext.celeryext import create_celery_app


def create_send_email_task(destination, subject, message):
    """
    Schedules a task to send an email
    :param destination:
    :param subject:
    :param message:
    :return:
    """

    # this is required for some unknown reason due to an initialisation problem with celery.
    create_celery_app(current_app)
    send_email.delay(destination, subject, message)


@shared_task
def send_email(destination, subject, message):
    try:
        connection = connect()
        mmp_msg = MIMEMultipart('alternative')
        mmp_msg['Subject'] = subject
        mmp_msg['From'] = current_app.config['MAIL_DEFAULT_SENDER']
        mmp_msg['To'] = destination

        part1 = MIMEText(message, 'html')
        mmp_msg.attach(part1)

        connection.sendmail(current_app.config['MAIL_DEFAULT_SENDER'], destination, mmp_msg.as_string())
        connection.quit()
    except Exception as e:
        print 'Exception occurred.'
        raise e


def connect():
    smtp = SMTP()
    smtp.connect(current_app.config['MAIL_SERVER'], current_app.config['MAIL_PORT'])
    if not current_app.config['SMTP_NO_PASSWORD']:
        smtp.login(current_app.config['MAIL_DEFAULT_SENDER'], current_app.config['MAIL_PASSWORD'])

    return smtp
