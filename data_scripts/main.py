from dedllogin import DEDL_auth
import requests
import json
import os
from getpass import getpass
from IPython.display import JSON
from utils import *
from dotenv import load_dotenv


def get_json_response(auth_headers, body):
    """
    Get json response from the HDA DestinE STAC API.
        
    :param auth_headers: Authentication headers for API call
    :param body: JSON body with parameters for API call
    :return: API response in JSON
    """
    response = requests.post("https://hda.data.destination-earth.eu/stac/search", 
                             headers=auth_headers, 
                             json=body)
    return response.json()

def get_download_url(asset):
    """ 
    Get download url and name from an asset in the feature list of Sentinel-3 OL2WFR data 
    
    :return: download url of asset, title of asset
    """
    return asset["href"], asset["title"]

def rm_files(files, folder_path):
    """
    Remove files in a given folder
        
    :param files: list of files you want to remove
    :param folder_path: folder path of files you want to remove
    :return: None
    """
    for asset in files:
        os.remove(os.path.join(folder_path,asset))

# Loads .env file with environment variables containing secrets
load_dotenv()
USERNAME=os.getenv('DEDL_USERNAME')
PASSWORD=os.getenv('DEDL_PASSWORD')
ACCESS_KEY=os.getenv('S3_ACCESS_KEY')
SECRET_KEY=os.getenv('S3_SECRET_KEY')

# Authenticate to the DEDL platform
auth = DEDL_auth(USERNAME, PASSWORD)

access_token = auth.get_token()
if access_token is not None:
    print("DEDL/DESP Access Token Obtained Successfully")
else:
    print("Failed to Obtain DEDL/DESP Access Token")

auth_headers = {"Authorization": f"Bearer {access_token}"}
# Connects to DEDL Islet S3 Service and returns a boto3 resource object
s3 = boto3_connect(ACCESS_KEY, SECRET_KEY)
# We are working with Sentinel-3 OLCI Level 2 Water Full Resolution
# Get all swaths intersecting with our bounding box in a given time period 
body = {
    "collections": ["EO.EUM.DAT.SENTINEL-3.OL_2_WFR___"],
    "datetime": "2019-06-01T00:00:00Z/2019-07-01T00:00:00Z",
    'bbox': [10,53,30,66] #latitude and longitude for Baltic Sea
}
response_json = get_json_response(auth_headers, body)
# We are only downloading specific assets, list them here.
download_assets=[
    "Oa06_reflectance.nc",
    "Oa08_reflectance.nc",
    "geo_coordinates.nc",
    "wqsf.nc"]
number_matched = response_json["numberMatched"]
number_processed = 0
# Multipage response, while-loop that goes through all of them.
while number_processed < number_matched:
    # We get 20 responses per page, loop through them.
    for feat in response_json["features"]:
        # Begin by downloading xdumanifest to check average cloud coverage
        s3_path=feat["properties"]['dedl:productIdentifier']
        url, name = get_download_url(feat["assets"]["xfdumanifest.xml"])
        download_feature(s3_path, url, name, auth_headers)
        # Get the cloud coverage and remove the file from local machine
        local_path = "./downloads" + s3_path
        cc = get_cloud_coverage(local_path)
        rm_files(["xfdumanifest.xml"], local_path)
        # If we have an average cloud coverage below 50 we download the file otherwise we just continue to the next
        if cc <= 50:
            print(f"Downloading {feat['id']}")
            # We only download the assets listed before
            for asset in download_assets:
                url, name = get_download_url(feat["assets"][asset])
                download_feature(s3_path, url, name, auth_headers)
            # try/except since we sometimes get broken .nc files, due to intermittent problems or data quality issues.
            # try opening the .nc files and to get the mask based on wqsf flags
            try:
                ds=xr.open_mfdataset([local_path + "/Oa06_reflectance.nc",local_path + "/Oa08_reflectance.nc", local_path + "/geo_coordinates.nc"])
                Oa08_mask = get_color_mask(local_path + "/wqsf.nc")
            except:
                # If any .nc files fails we append some information to a text file so that we can try and re-process them at a later point if needed.
                print("Faulty nc files")
                with open("not_completed_files.txt", "a") as myfile:
                    lines = [local_path,"zip-download link:",feat["assets"]["downloadLink"]["href"]]
                    myfile.write("\n".join(lines) + "\n")
                # Remove local files and continue to the next feature
                rm_files(download_assets, local_path)
                continue
            # If try/except is successful we proceed to mask, resample and set projection of the data
            maskOa08 = apply_mask(Oa08_mask, ds["Oa08_reflectance"].data.compute())
            maskOa06 = apply_mask(Oa08_mask, ds["Oa06_reflectance"].data.compute())
            lons = ds["longitude"] 
            lats = ds["latitude"]
            ds.close()
            # Resample, clip and project
            result06, areadef06=project_and_resample("Oa06_baltic_sea_WGS84", maskOa06, lons, lats)
            result08, areadef08=project_and_resample("Oa08_baltic_sea_WGS84", maskOa08, lons, lats)
            mask_result = [apply_mask(result06.mask, result06.data), apply_mask(result08.mask, result08.data)]
            # Write result to local and then to s3
            to_raster(mask_result, local_path + "/Oa0608_reflectance_masked.tif", result06.shape, areadef06.resolution, 2)
            write_to_s3(s3, "algaestorm", s3_path , local_path, "Oa0608_reflectance_masked.tif")
            # Remove from local
            rm_files(download_assets, local_path)
    # Get next page                
    number_processed += response_json["numberReturned"]
    if response_json.get("links")[-1].get("rel") == "next":
        # try/except since token might expire.
        # try to get the json response of the next page.
        try:
            response_json = get_json_response(auth_headers, response_json["links"][-1]["body"])
        except:
            # Get a new access token and try to get the next page again
            access_token = auth.get_token()
            auth_headers = {"Authorization": f"Bearer {access_token}"}
            response_json = get_json_response(auth_headers, response_json["links"][-1]["body"])