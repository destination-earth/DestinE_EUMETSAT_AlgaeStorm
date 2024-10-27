import eumartools
import numpy as np
import xarray as xr
import rioxarray
import rasterio
import shapely
import json
from osgeo import gdal
from osgeo import osr
import numpy as np
import os
import sys
from pyresample import create_area_def
from pyproj.crs import CRS
import pyresample
from pyresample.kd_tree import resample_nearest
from pyresample.geometry import AreaDefinition
import requests
import time
from getpass import getpass
import boto3
from bs4 import BeautifulSoup
from itertools import product
from rasterio import windows
from rasterio import MemoryFile
from io import BytesIO

 
def apply_mask(mask, data_array):
    """
    Takes a mask and array and returns array with masked values set to NULL.
    
    :param mask: A True/False numpy array 
    :param data_array: A numpy array
    :return: A numpy array with masked values set to NULL
    """

    mask = mask.astype(float)
    mask[mask == 1.0] = np.nan
    mask[np.isfinite(mask)] = 1.0
    return data_array * mask

def get_color_mask(path):
    """
    Takes a path to a WQSF .nc file and returns a masked array based on
    WQSF flags
    
    :param path: Str path to WQSF .nc file
    :return: A numpy masked array with listed ocean colour flags masked out
    """
    ocean_clr_flags=['LAND', 'CLOUD', 'CLOUD_AMBIGUOUS', 'CLOUD_MARGIN', 
    'INVALID', 'COSMETIC', 'SATURATED', 'SUSPECT',
    'HISOLZEN', 'HIGHGLINT', 'SNOW_ICE', 'AC_FAIL',
    'WHITECAPS', 'ADJAC', 'RWNEG_O2', 'RWNEG_O3',
    'RWNEG_O4', 'RWNEG_O5', 'RWNEG_O6', 'RWNEG_O7', 'RWNEG_O8']
    return eumartools.flag_mask(path, 'WQSF', ocean_clr_flags)

def project_and_resample(area_name, data_array, lons, lats):
    """
    Uses pyresample to cut and resample to the Baltic Sea bounding box specified 
    Sets projection to EPSG:4326.
    
    :param area_name: An area name of your choice
    :param data_array: A numpy pixel value array
    :param lons: An array with longitudes covering the original area
    :param lats: An array with latitudes covering the original area
    :return: An array with the new shape and resampled pixel values, the area defition in cartopy CRS format
    """
    # Area definition that we want to resample to
    area_def = create_area_def(area_name,
                               CRS.from_epsg(4326),
                               area_extent=[10,53,30,66],
                               # Approx 300x300m in pixel size
                               resolution=(0.0027,0.0027),
                               units='degrees',
                               description='Baltic Sea degree lat-lon grid')
    # Definition of the swath based on the lat/lon of the original area
    swath_def = pyresample.geometry.SwathDefinition(lons, lats)
    result = resample_nearest(swath_def, data_array, area_def, radius_of_influence=20000, fill_value=None)
    return result, area_def

def to_raster(data_arrays, output_path, raster_shape, res, num_bands):
    """
    Write multiple arrays to a single raster with EPSG:4326 as projection.
    The function will write the arrays as bands to the raster in the same order as they are
    passed in the data arrays parameter. The number of bands must match the number of arrays
    in data_arrays. User needs to specify resolution and shape of the array. 
    The bounding box is set to Baltic Sea.
    
    :param data_arrays: A list of numpy pixel value arrays
    :param output_path: Local path where the new raster should be written to
    :param raster_shape: The (x,y) shape of the pixel value array
    :param res: The resolution of the raster in format (xres, yres)
    :num_bands: The number of bands you wish to write to the raster
    :return: None
    """
    image_size = raster_shape
    #  Choose some Geographic Transform, in this case around Baltic Sea
    lat = [53,66]
    lon = [10,30]
    # set geotransform
    nx = image_size[0]
    ny = image_size[1]
    xmin, ymin, xmax, ymax = [min(lon), min(lat), max(lon), max(lat)]
    geotransform = (xmin, res[0], 0, ymax, 0, -res[1])
    # create the 1-band raster file
    dst_ds = gdal.GetDriverByName('GTiff').Create(output_path, ny, nx, num_bands, gdal.GDT_Float32)
    dst_ds.SetGeoTransform(geotransform)    # specify coords
    srs = osr.SpatialReference()            # establish encoding
    srs.ImportFromEPSG(4326)                # WGS84 lat/long
    dst_ds.SetProjection(srs.ExportToWkt()) # export coords to file
    for i in range(num_bands):
        dst_ds.GetRasterBand(i + 1).WriteArray(data_arrays[i])   # write r-band to the raster
    dst_ds.FlushCache()                     # write to disk
    dst_ds = None

def download_feature(s3_path, download_url, filename, auth_headers):
    """
    Downloads a single feature locally. The feature is entirely dependent on the download_url,
    can be a zip directory or a single .nc file.
    
    :param s3_path: The path that would be used in s3. Simplifies catalouge
    :param download url: The url where we can download the file from. Taken from metadata in response
    :param filename: The desired filename
    :param auth_headers: Authentication headers for API call
    :return: None
    """
    response = requests.get(download_url, headers=auth_headers)
    total_size = int(response.headers.get("content-length", 0))
    rootpath = f"downloads/{s3_path}/"
    if not os.path.exists(rootpath):
        os.makedirs(rootpath)
    with open(os.path.join(rootpath, filename), 'wb') as f:
        for data in response.iter_content(1024):
            f.write(data)

def boto3_connect(access_key, secret_key):
    """
    Returns a boto3 s3 resource that can be used for s3 operations.
    
    :param access_key: The AWS access key for your s3 bucket
    :param secret_key: The AWS secret key for your s3 bucket
    :return: A boto3 s3 resource
    """
    host='https://s3.central.data.destination-earth.eu'
    s3=boto3.resource('s3',aws_access_key_id=access_key,
    aws_secret_access_key=secret_key, endpoint_url=host)
    return s3

def get_s3_obj(boto_resource, bucketname, s3_path, filename):
    """
    Returns a boto3 s3 bucket object that can be used for bucket operations.
    
    :param boto_resource: A boto3 s3 resource
    :param bucketname: The s3 bucket name
    :param s3_path: The s3 path that contains the object (not including the object itself)
    :param filename: The filename that will in the end become your object
    :return: A boto3 s3 bucket object
    """
    # The identifier of an object is the full path including the filename.
    # e.g. eodata/Sentinel-3/OLCI/OL_2_WFR/2019/06/01/S3B_OL_2_WFR____20190601T083424_20190601T083724_20190602T165416_0179_026_064_1980_MAR_O_NT_002.SEN3/Oa08_reflectance_masked.tif
    identifier=os.path.join(s3_path, filename).strip('/')
    return boto_resource.Object(bucketname, identifier)
                
def write_to_s3(boto_resource, bucketname, s3_path, input_filepath, filename):
    """
    Writes a local file to s3
    
    :param boto_resource: A boto3 s3 resource
    :param bucketname: The s3 bucket name
    :param s3_path: The s3 path that you want to put a file in
    :param input_filepath: The folder of where your local file resides
    :param filename: The filename of the file you wish to write to s3
    :return: None
    """
    s3obj = get_s3_obj(boto_resource, bucketname, s3_path, filename)
    local_path = os.path.join(input_filepath,filename)
    with open(local_path, 'rb') as f:
        result = s3obj.put(Body=f)
        res = result.get('ResponseMetadata')
        if res.get('HTTPStatusCode') == 200:
            print('File Uploaded Successfully')
            os.remove(local_path)
        else:
            print('File Not Uploaded')
            with open("not_completed_files.txt", "a") as myfile:
                myfile.write(local_path)
                os.remove(local_path)

def get_cloud_coverage(path):
    """
    Get the cloud coverage from the xfdumanifest XML file.
    
    :param path: Folder of the xfdumanifest file
    :return: Cloud coverage as percentage number between 0 and 100
    """
    filepath=os.path.join(path, "xfdumanifest.xml")
    with open(filepath, 'r') as f:
        bs_data = BeautifulSoup(f.read(), "xml")
        cloud_coverage=bs_data.find('sentinel3:cloudyPixels')
        result = 100 if cloud_coverage==-1 or cloud_coverage is None else float(cloud_coverage.attrs["percentage"]) 

    return result

def get_split_tiles(ds, width=256, height=256):
    """
    Splits a tif file into tiles/windows and yields each tile and transform of that tile
    
    :param ds: A rasterio raster dataset
    :param width: The width of the tile
    :param height: The height of the tile
    :yield: A raster tile of the specified height and width, the transform of the tile
    """
    nols, nrows = ds.meta['width'], ds.meta['height']
    offsets = product(range(0, nols, width), range(0, nrows, height))
    big_window = windows.Window(col_off=0, row_off=0, width=nols, height=nrows)
    for col_off, row_off in  offsets:
        window =windows.Window(col_off=col_off, row_off=row_off, width=width, height=height).intersection(big_window)
        transform = windows.transform(window, ds.transform)
        yield window, transform

def split_s3_tif_and_write(boto_resource, bucketname, s3_tif_obj, sent3_id, output_s3_path, dt):
    """
    Splits a raster .tif file from s3 and writes the tiles back to s3
    
    :param boto_resource: A boto3 s3 resource
    :param bucketname: The s3 bucket name
    :param s3_tif_obj: boto3 object representation of a tif file in a bucket
    :param sent3_id: The id of the sentinel-3 product, e.g. S3B_OL_2_WFR____20190601T083424_20190601T083724_20190602T165416_0179_026_064_1980_MAR_O_NT_002.SEN3
    :param output_s3_path: The s3 path where you want to put the tiles (not including the name of the tiles themselves)
    :param dt: Timestamp of the raster 
    :return: None
    """
    # Open original tif file
    body = s3_tif_obj.get()['Body'].read()
    filelike = BytesIO(body)
    output_filename = 'tile_{}-{}.tif'
    with rasterio.open(filelike) as inds:
        tile_width, tile_height = 1000, 1000

        meta = inds.meta.copy()
        # Split into tiles
        for window, transform in get_split_tiles(inds, tile_width, tile_height):
            meta['transform'] = transform
            meta['width'], meta['height'] = window.width, window.height
            outpath = output_s3_path + sent3_id + "/"
            outfile = os.path.join(outpath, output_filename.format(int(window.col_off), int(window.row_off)))
            # Write the tile into a Memoryfile
            write_obj = boto_resource.Object(bucketname, outfile)
            with MemoryFile() as memfile:
                with memfile.open(**meta) as dataset:
                    dataset.update_tags(date=dt)
                    dataset.write(inds.read(window=window))
                # Write the file to s3
                result = write_obj.put(Body=memfile)
            res = result.get('ResponseMetadata')
            if res.get('HTTPStatusCode') != 200:
                print(f'File Not Uploaded {outfile}')