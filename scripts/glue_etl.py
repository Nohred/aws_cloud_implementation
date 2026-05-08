from awsglue.context import GlueContext
from pyspark.context import SparkContext
from awsglue.utils import getResolvedOptions
import sys
import boto3
from PIL import Image
import io
import os

args = getResolvedOptions(sys.argv,
                          [
                              'JOB_NAME',
                              'input_bucket',
                              'output_bucket',
                              'size'
                          ])

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

INPUT_BUCKET = args.get('input_bucket')
OUTPUT_BUCKET = args.get('output_bucket')
SIZE = int(args.get('size', '256'))

def get_optional_argument(name, default_value):
    flag = f'--{name}'
    if flag in sys.argv:
        position = sys.argv.index(flag)
        if position + 1 < len(sys.argv):
            return sys.argv[position + 1]
    return default_value


OUTPUT_PREFIX = get_optional_argument('output_prefix', 'resized/')

def process_pair(pair):
    path, portable = pair
    try:
        if hasattr(portable, 'read'):
            data = portable.read()
        elif isinstance(portable, (bytes, bytearray, memoryview)):
            data = bytes(portable)
        else:
            raise TypeError(f'Unsupported binary payload type: {type(portable)!r}')

        img = Image.open(io.BytesIO(data)).convert('RGB')
        img = img.resize((SIZE, SIZE))
        out_buf = io.BytesIO()
        img.save(out_buf, format='JPEG')
        out_buf.seek(0)

        # Derive output key
        basename = os.path.basename(path)
        s3_key = f"{OUTPUT_PREFIX.rstrip('/')}/{basename}"

        s3 = boto3.client('s3')
        s3.put_object(Bucket=OUTPUT_BUCKET, Key=s3_key, Body=out_buf.getvalue(), ContentType='image/jpeg')
        return (path, 'SUCCEEDED')
    except Exception as e:
        return (path, f'FAILED: {e}')


def main():
    s3_path = f's3://{INPUT_BUCKET}/*'

    rdd = sc.binaryFiles(s3_path)
    results = rdd.map(process_pair).collect()
    for r in results:
        print(r)


if __name__ == '__main__':
    main()
