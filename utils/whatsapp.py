import os
import mimetypes
import requests
from django.conf import settings
from decouple import config

API_VERSION = "v22.0"


def get_whatsapp_config():
    token = getattr(settings, "WHATSAPP_TOKEN", "") or config("WHATSAPP_TOKEN", default="")
    phone_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "") or config("WHATSAPP_PHONE_NUMBER_ID", default="")
    base_url = f"https://graph.facebook.com/{API_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    return base_url, headers


# -----------------------------
# UPLOAD MEDIA DIRECTLY TO META
# -----------------------------
def upload_whatsapp_media(file_obj, filename=None, mime_type=None):
    """
    Uploads a file directly to Meta WhatsApp Cloud API /media endpoint.
    Returns media ID string or None on failure.
    """
    token = getattr(settings, "WHATSAPP_TOKEN", "") or config("WHATSAPP_TOKEN", default="")
    phone_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "") or config("WHATSAPP_PHONE_NUMBER_ID", default="")
    url = f"https://graph.facebook.com/{API_VERSION}/{phone_id}/media"

    fname = filename or getattr(file_obj, "name", "file")
    if hasattr(fname, "split"):
        fname = os.path.basename(fname)

    if not mime_type:
        mime_type = mimetypes.guess_type(fname)[0] or "application/octet-stream"

    if hasattr(file_obj, "read"):
        file_obj.seek(0)
        file_bytes = file_obj.read()
    elif isinstance(file_obj, str) and os.path.exists(file_obj):
        with open(file_obj, "rb") as f:
            file_bytes = f.read()
    else:
        file_bytes = file_obj

    files = {
        "file": (fname, file_bytes, mime_type)
    }
    data = {"messaging_product": "whatsapp"}
    headers = {"Authorization": f"Bearer {token}"}

    try:
        res = requests.post(url, headers=headers, data=data, files=files).json()
        if res.get("id"):
            return res["id"]
        if res.get("error"):
            print("Upload media error:", res["error"])
    except Exception as e:
        print("Upload media exception:", e)
    return None


# -----------------------------
# FORMAT PHONE NUMBER
# -----------------------------
def format_phone(number):
    if not number:
        return None
    number = str(number).strip().replace(" ", "").replace("-", "")
    if number.startswith("+"):
        number = number[1:]
    if len(number) == 10 and number.isdigit():
        return "91" + number
    if len(number) >= 11 and number.isdigit():
        return number
    return None


# -----------------------------
# SEND TEXT MESSAGE
# -----------------------------
def send_whatsapp_text(to, message):
    base_url, headers = get_whatsapp_config()
    to = str(to).lstrip("+").strip()
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    return requests.post(base_url, headers=headers, json=payload).json()


# -----------------------------
# SEND IMAGE
# -----------------------------
def send_whatsapp_image(to, image_source, caption=None):
    base_url, headers = get_whatsapp_config()
    to = str(to).lstrip("+").strip()

    image_source_str = str(image_source).strip()
    if image_source_str.isdigit():
        image_data = {"id": image_source_str}
    else:
        image_data = {"link": image_source_str}

    if caption:
        image_data["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": image_data
    }
    return requests.post(base_url, headers=headers, json=payload).json()


# -----------------------------
# SEND DOCUMENT (PDF)
# -----------------------------
def send_whatsapp_document(to, doc_source, filename="document.pdf"):
    base_url, headers = get_whatsapp_config()
    to = str(to).lstrip("+").strip()

    doc_source_str = str(doc_source).strip()
    if doc_source_str.isdigit():
        doc_data = {"id": doc_source_str, "filename": filename}
    else:
        doc_data = {"link": doc_source_str, "filename": filename}

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "document",
        "document": doc_data
    }
    return requests.post(base_url, headers=headers, json=payload).json()


def send_whatsapp_template(to, template_name="hello_world", template_params=None, language_code="en_US"):
    base_url, headers = get_whatsapp_config()
    to = str(to).lstrip("+").strip()

    template_obj = {
        "name": template_name,
        "language": {"code": language_code}
    }
    if template_params:
        template_obj["components"] = [
            {
                "type": "body",
                "parameters": template_params
            }
        ]

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template_obj
    }

    return requests.post(base_url, headers=headers, json=payload).json()
