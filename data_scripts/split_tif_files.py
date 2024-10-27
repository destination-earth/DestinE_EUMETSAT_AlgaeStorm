from utils import split_s3_tif_and_write, boto3_connect
import os
from dotenv import load_dotenv
import boto3
from datetime import datetime, timedelta
import pdb


load_dotenv()
ACCESS_KEY=os.getenv('S3_ACCESS_KEY')
SECRET_KEY=os.getenv('S3_SECRET_KEY')
s3 = boto3_connect(ACCESS_KEY, SECRET_KEY)
client = boto3.client("s3", aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY, endpoint_url='https://s3.central.data.destination-earth.eu')
bucketname='algaestorm'
start_date = datetime(2019, 6, 1)
end_date = datetime(2019, 6, 30)

# Initialize an empty list to store the dates
date_list = []

# Loop through each day from start_date to end_date
current_date = start_date
rootpath = 'eodata/Sentinel-3/OLCI/OL_2_WFR/'
while current_date <= end_date:
    # Format the date as YYYY/MM/DD and add to the list
    prefix = rootpath + current_date.strftime('%Y/%m/%d') + "/"
    s3b = client.list_objects_v2(Bucket=bucketname, Prefix=prefix + "S3B")
    s3a = client.list_objects_v2(Bucket=bucketname, Prefix=prefix + "S3A")
    s3b_filter = filter(lambda obj: obj['Key'].endswith('Oa0608_reflectance_masked.tif'), s3b.get("Contents")) if s3b["KeyCount"] >0 else []
    s3a_filter = filter(lambda obj: obj['Key'].endswith('Oa0608_reflectance_masked.tif'), s3a.get("Contents")) if s3a["KeyCount"]>0 else []
    # Pick the first product for each day, prioritizing 3A over 3B
    s3b_lst = list(s3b_filter)
    s3a_lst = list(s3a_filter)
    if s3a_lst:
        # Split the tif file and write to bucket
        first = s3a_lst[0]
        s3obj = s3.Object(bucketname, first["Key"])
        split_s3_tif_and_write(s3, bucketname, s3obj, first["Key"].split("/")[-2], "eodata/split_img_Oa06/", current_date.strftime('%Y-%m-%d'))
        print(current_date)
        print(first["Key"].split("/")[-1])
    elif s3b_lst:
        # Split the tif file and write to bucket
        first = s3b_lst[0]
        s3obj = s3.Object(bucketname, first["Key"])
        split_s3_tif_and_write(s3, bucketname, s3obj, first["Key"].split("/")[-2], "eodata/split_img_Oa06/", current_date.strftime('%Y-%m-%d'))
        print(current_date)
        print(first["Key"].split("/")[-1])
    else:
        print(f"no product in folder: {prefix}")
    # Move to the next day
    current_date += timedelta(days=1)