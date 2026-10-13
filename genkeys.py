"""Makes the two keys web push needs. Run once:  python genkeys.py"""
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
private = key.private_numbers().private_value.to_bytes(32, "big")
public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print("Add these two variables in Railway (and in your .env to try it locally):\n")
print("VAPID_PRIVATE_KEY=" + b64(private))
print("VAPID_PUBLIC_KEY=" + b64(public))
