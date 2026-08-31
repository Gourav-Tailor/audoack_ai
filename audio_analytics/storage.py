import boto3
from botocore.config import Config
from django.conf import settings
from storages.backends.s3 import S3Storage


class MinIOStorage(S3Storage):
    """
    MinIO storage backend.

    Internal endpoint:
        http://minio:9000

    Public browser endpoint:
        https://audoack.in

    The public endpoint is intentionally the same HTTPS hostname served by
    the Google HTTPS Load Balancer. Nginx routes /<bucket>/... internally to
    MinIO, so browsers never need to access MinIO on port 9000 directly.
    """

    def url(self, name, parameters=None, expire=None, http_method=None):
        if expire is None:
            expire = settings.AWS_QUERYSTRING_EXPIRE

        client = boto3.client(
            "s3",
            endpoint_url=settings.MINIO_PUBLIC_ENDPOINT_URL,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
            config=Config(
                signature_version="s3v4",
                s3={
                    "addressing_style": settings.AWS_S3_ADDRESSING_STYLE,
                },
            ),
        )

        params = {
            "Bucket": self.bucket_name,
            "Key": name,
        }

        if parameters:
            params.update(parameters)

        return client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expire,
            HttpMethod=http_method or "GET",
        )
