import pathlib
import pyproj
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import plotly
import requests
import rioxarray as rxr
from sqlalchemy import create_engine
import streamlit as st

st.set_page_config(page_title='Well Profiler App',
                   page_icon=':material/full_stacked_bar_chart:',
                   layout='wide')

CRS_LIST = pyproj.database.query_crs_info()
CRS_STR_LIST = [f"{crs.auth_name}:{crs.code} - {crs.name}" for crs in CRS_LIST]
CRS_DICT = {f"{crs.auth_name}:{crs.code} - {crs.name}": crs for crs in CRS_LIST}
IL_LIDAR_URL=r"https://data.isgs.illinois.edu/arcgis/services/Elevation/IL_Statewide_Lidar_DEM_WGS/ImageServer/WMSServer?request=GetCapabilities&service=WMS"
GMRT_BASE_URL = r"https://www.gmrt.org:443/services/GridServer?minlongitude&maxlongitude%2C%20&minlatitude&maxlatitude&format=geotiff&resolution=default&layer=topo"
RASTER_SRC_DICT = {"ISGS Statewide Lidar":IL_LIDAR_URL,
                   "Global Multi-Resolution Topography (~30m)":GMRT_BASE_URL,
                   "Other Web Service":"get_service_info",
                   "Raster file":"get_file_name"
                   }

# Establish values for default CRS
DEFAULT_POINTS_CRS = "EPSG:4326 - WGS 84" # "EPSG:6345 - NAD83(2011) / UTM zone 16N"
DEFAULT_POINTS_CRS_INDEX = CRS_STR_LIST.index(DEFAULT_POINTS_CRS)
DEFAULT_OUTPUT_CRS = DEFAULT_POINTS_CRS


def main():
    with st.container():
        well, elevTab, profileTab = st.tabs(["Wells", "Elevation", "Profile"])
        wellincol, wellindfcol = st.columns([0.5, 0.5])
        wellincol.file_uploader("Upload Well Data", key='well_uploader', on_change=ingest_table)
        #parsecol.button('Parse Well Data', key='well_parse_button',on_click=ingest_table)

        colDisabled = False
        if not hasattr(st.session_state, "well_columns"):
            st.session_state.well_columns = []
            colDisabled = True

        xcol, ycol, crscol = wellincol.columns([0.3,0.3,0.6])
        xcol.selectbox('X Coordinate Column', key='xcoord_col', options=st.session_state.well_columns,
                       disabled=colDisabled)
        ycol.selectbox('Y Coordinate Column', key='ycoord_col', options=st.session_state.well_columns,
                       disabled=colDisabled)
        
        crscol.selectbox(label="Well Coordinates CRS", options=CRS_STR_LIST, index=DEFAULT_POINTS_CRS_INDEX)

def read_study_area():
    sa_gdf = gpd.read_file()
    
    st.session_state.study_area = sa_gdf

def get_utm_crs():
    from pyproj.database import query_utm_crs_info
    from pyproj.aoi import AreaOfInterest

    # Example: coordinates for Chicago, IL
    lon = st.session_state.longitude
    lat = st.session_state.latitude

    utm_crs_list = query_utm_crs_info(
        datum_name="WGS 84",
        area_of_interest=AreaOfInterest(
            west_lon_degree=lon,
            south_lat_degree=lat,
            east_lon_degree=lon,
            north_lat_degree=lat,
        ),
        )

    # The first result is the best match
    utm_crs = utm_crs_list[0]
    st.session_state.utm_crs_code = utm_crs.code

def ingest_table():

    # Can be used wherever a "file-like" object is accepted:
    welldf = pd.read_csv(st.session_state.well_uploader)
    st.session_state.well_columns = welldf.columns

    

    blocsdf = boreholeLoc_df[['API10', 'LONGITUDE', "LATITUDE"]]
    downholeLocsDF = pd.merge(left=downhole_df, right=blocsdf, how='inner',left_on='api10', right_on='API10')
    pvCountyList = ['Whiteside', 'Bureau', "Henry", "Lee", "Rock Island"]
    pvDF = downholeLocsDF[downholeLocsDF['CNTYNAME'].isin(pvCountyList)]
    pvDF = pvDF[pvDF["LONGITUDE"] > -91.5]
    pvDF = pvDF[pvDF["LONGITUDE"] < -88.5]
    pvDF = pvDF[pvDF["LATITUDE"] > 41]
    pvDF = pvDF[pvDF["LATITUDE"] < 42]
    pvDF.plot(x='LONGITUDE', y="LATITUDE", kind='scatter', s=0.1, c='k')


if __name__ == "__main__":
    main()
