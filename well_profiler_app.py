from io import BytesIO
import pathlib

from streamlit_folium import st_folium
from folium.plugins import Draw
import folium

import geopandas as gpd
import matplotlib.pyplot as plt
from owslib.wms import WebMapService
import pandas as pd
import plotly
import pyproj
import requests
import rioxarray as rxr
from shapely.geometry import LineString
from sqlalchemy import create_engine
import streamlit as st

st.set_page_config(page_title='Well Profiler App',
                   page_icon=':material/full_stacked_bar_chart:',
                   layout='wide')

CRS_LIST = pyproj.database.query_crs_info()
CRS_STR_LIST = [f"{crs.auth_name}:{crs.code} - {crs.name}" for crs in CRS_LIST]
CRS_DICT = {f"{crs.auth_name}:{crs.code} - {crs.name}": crs for crs in CRS_LIST}
IL_LIDAR_URL = r"https://data.isgs.illinois.edu/arcgis/services/Elevation/IL_Statewide_Lidar_DEM_WGS/ImageServer/WMSServer?request=GetCapabilities&service=WMS"
GMRT_BASE_URL = r"https://www.gmrt.org:443/services/GridServer?minlongitude&maxlongitude%2C%20&minlatitude&maxlatitude&format=geotiff&resolution=default&layer=topo"
RASTER_SRC_DICT = {"ISGS Statewide Lidar": IL_LIDAR_URL,
                   "Global Multi-Resolution Topography (~30m)": GMRT_BASE_URL,
                   "Other Web Service": "get_service_info",
                   "Raster file": "get_file_name"
                   }

# Establish values for default CRS
DEFAULT_POINTS_CRS = "EPSG:4326 - WGS 84"  # "EPSG:6345 - NAD83(2011) / UTM zone 16N"
DEFAULT_POINTS_CRS_INDEX = CRS_STR_LIST.index(DEFAULT_POINTS_CRS)
DEFAULT_OUTPUT_CRS = DEFAULT_POINTS_CRS


def main():
    with st.container(width='stretch', height='stretch'):
        titleCol, projCol, menuCol, tCol = st.columns([0.35, 0.1, 0.05, 0.5],
                                                  vertical_alignment='bottom')
        titleCol.title('Well Profiler')
        projCol.selectbox(label="Project CRS", options=CRS_STR_LIST,
                          index=DEFAULT_POINTS_CRS_INDEX,
                          key='project_crs')
        menuCol.menu_button('TestMenu', options=['OPTION 1', 'option 2'])

        # Make tabs in main container
        wellTab, st.session_state.tableTab, profileTab = st.tabs(["Wells", "Well Table", "Profile"])

        # Make columns in well tab
        wellincol, st.session_state.mapCol = wellTab.columns([0.5, 0.5])

        # Profile section
        st.session_state.mapCol.header('Specify Profile', divider='rainbow')
        pillCol, uploadCol, pCRSCol = st.session_state.mapCol.columns([0.2, 0.6, 0.2],)
        pillCol.pills('Profile Input Type', options=['Upload', 'Selection'],
                      default='Selection',
                      on_change=profile_type_update,
                      disabled=False, key='profile_type')
        if st.session_state.profile_type == 'Upload':
            uploadCol.file_uploader("Upload Profile Data",
                                    key='profile_uploader',
                                    on_change=ingest_profile)
            pCRSCol.selectbox("Profile CRS",
                              options=CRS_STR_LIST,
                              index=DEFAULT_POINTS_CRS_INDEX,
                              key='profile_crs')
        else:
            st.session_state.profile_crs = DEFAULT_POINTS_CRS
            uploadCol.header("Use map to draw profile", text_alignment='right')
        bSizeCol, bUnitCol, bEmptyCol = st.session_state.mapCol.columns([0.2, 0.2, 0.6])
        bSizeCol.number_input("Buffer size", min_value=0.0, format="%0.1f",
                              value=5.0,
                              key='buffer_size')
        bUnitCol.selectbox('Buffer Size Unit',
                           options=['feet', 'meters', 'kilometers', 'miles'],
                           index=2, key='buffer_unit')

        # Well Data section
        wellincol.header('Specify Well Data', divider='rainbow')
        wellincol.pills("Well Data Source",
                        options=['Table upload', 'Database'],
                        default='Table upload',
                        key='well_source')
        
        if st.session_state.well_source == 'Table upload':
            wellincol.file_uploader("Upload Well Data",
                                key='well_uploader', on_change=ingest_table)
        else:
            st.session_state.well_df_IN = ingest_table()
            st.session_state.tableTab.dataframe(st.session_state.well_df_IN)

        colDisabled = False
        if not hasattr(st.session_state, "well_columns"):
            st.session_state.well_columns = []
            colDisabled = True

        xcol, ycol, crscol = wellincol.columns([0.3, 0.3, 0.6])
        xcol.selectbox('X Coordinate Column', key='xcoord_col',
                       options=st.session_state.well_columns,
                       disabled=colDisabled)
        ycol.selectbox('Y Coordinate Column', key='ycoord_col',
                       options=st.session_state.well_columns,
                       disabled=colDisabled)

        crscol.selectbox(label="Well Coordinates CRS", options=CRS_STR_LIST,
                         index=DEFAULT_POINTS_CRS_INDEX,
                         key='well_input_crs')
        
        # Elevation input
        wellincol.header('Elevation', divider='rainbow')
        eSourceCol, eSpecCol = wellincol.columns([0.4, 0.6],
                                                 vertical_alignment='bottom')
        eSourceCol.pills('Elevation Source',
                         options=["Input table",
                                 "Global dataset (GMRT)",
                                 "Illinois lidar"],
                         default='Illinois lidar', key='elev_source')
        eSource = st.session_state.elev_source
        if eSource == 'Illinois lidar':
            eSpecCol.write('Using ISGS statewide lidar dataset.')
        elif eSource == 'Input table':
            eColOpts = []
            if hasattr(st.session_state, 'well_columns'):
                eColOpts = st.session_state.well_columns
            eSpecCol.selectbox("Specify Elevation Column",
                               options=eColOpts)
        else:
            eSpecCol.write('Using Global Multiresolution Topography dataset.')

        with st.session_state.mapCol:
            if "current_profile" not in st.session_state:
                st.session_state.current_profile = None
            if "new_map_draw" not in st.session_state:
                st.session_state.new_map_draw = False
            if 'map_result' not in st.session_state:
                st.session_state.map_result = {'last_active_drawing':None}

            if not st.session_state.new_map_draw:
                print("DRAWING BASE MAP")
                draw_base_map()
                st.session_state.new_map_draw = False
            else:
                m = folium.Map(
                    location=[40.0, -89.0],
                    zoom_start=7,
                    )
                folium.PolyLine(
                    locations=[(lat, lon) for lon, lat in st.session_state.current_profile.coords],  # flip to (lat, lon)
                    color="blue",
                    weight=3,
                    opacity=0.8,
                    tooltip="Current Profile",
                    popup="Current Profile",
                ).add_to(m)

                draw_base_map(map=m)
                st.session_state.new_map_draw = True

def draw_base_map(map=None):
    if map is None:
        m = folium.Map(
            location=[40.0, -89.0],
            zoom_start=7,
            )
    else:
        m = map
    # Add elevation basemap
    #lidarUrl = IL_LIDAR_URL.split('?request')[0]
    #folium.WmsTileLayer(
    #    url=lidarUrl,
    #    name="IL Statewide LiDAR DEM",
    #    layers="None",
    #    styles="",
    #    fmt="image/png",
    #    version="1.3.0",
    #    transparent=True,
    #    attr="Illinois State Geological Survey",
    #    overlay=True,
    #    control=True,
    #    # NO crs= here
    #).add_to(_map)

    if hasattr(st.session_state, 'profile_buffer') and isinstance(st.session_state.profile_buffer, gpd.GeoDataFrame):
        folium.GeoJson(
            st.session_state.profile_buffer,
            name="Profile Buffer",
            style_function=lambda x: {
                "fillColor": "black",
                "color": "black",
                "weight": 0,
                "fillOpacity": 0.1,
            }
        ).add_to(m)

        folium.PolyLine(
            locations=[(lon, lat) for lon, lat in st.session_state.current_profile.coords],
            name="Current Profile",
            color='black',
            weight=3,
        ).add_to(m)

        bounds = st.session_state.profile_buffer.total_bounds

        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]],
                     padding=[50, 50])

    # Show the draw tool
    Draw(
        export=False,
        draw_options={
            "polyline": {"shapeOptions": {"color": "red", "weight": 3}},
            "polygon": False,
            "rectangle": False,
            "circle": False,
            "circlemarker": False,
            "marker": False,
        },
        edit_options={"edit": True, "remove": True},
    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    #fg = folium.FeatureGroup(name="Current Line")

    #if st.session_state.current_profile:
    #    folium.PolyLine(
    #        locations=[(lat, lon) for lon, lat in st.session_state.current_profile],
    #        color="red",
    #        weight=3,
    #    ).add_to(fg)
    with st.session_state.mapCol:
        st_folium(m,
            #feature_group_to_add=fg,
            use_container_width=True,
            on_change=check_map_drawings,
            returned_objects=['last_active_drawing'],
            key='map_result')

def check_map_drawings():
    print("CHECKING MAP DRAWINGS!!")
    if st.session_state.map_result['last_active_drawing'] is not None:
        print(st.session_state.map_result['last_active_drawing']['geometry']['coordinates'])
    else:
        print("NO LAST ACTIVE DRAWING")
    coords = st.session_state.map_result['last_active_drawing']['geometry']['coordinates']
    st.session_state.current_profile = LineString([[lat, lon] for lon, lat in coords])
    make_profiles()

def make_profiles():
    if st.session_state.profile_type == 'Upload':
        try:
            profile = st.session_state.profile_from_file
        except Exception:
            st.info("No Profile data has been uploaded. Either use 'Selection' as the Profile input type or upload a geospatial file with LineStrings")
    else:
        profile = st.session_state.current_profile
        profile = LineString([(y, x) for x, y in profile.coords])

    profileDict = {"Labels": ["Profile"], 
                   'geometry': [profile]}
    profileGDF = gpd.GeoDataFrame(profileDict,
                                  crs=st.session_state.profile_crs.split(' ')[0])

    profileWGS84 = profileGDF.to_crs("EPSG:4326")
    centroid = profileWGS84.centroid
    utmCRS = get_utm_crs(centroid[0])
    profileUTM = profileGDF.to_crs(utmCRS)

    mConvertFactor = {'feet': 0.3048,
                      'meters': 1,
                      'kilometers': 1000,
                      'miles': 1609.344}
    buffSize_m = st.session_state.buffer_size * mConvertFactor[st.session_state.buffer_unit]
    profileUTMBuffer = profileUTM.buffer(distance=buffSize_m)

    profileUTMBuffer = gpd.GeoDataFrame(profileUTMBuffer, geometry=0)
    st.session_state.profile_buffer = profileUTMBuffer.to_crs("EPSG:4326")

    if hasattr(st.session_state, 'points_df') and isinstance(st.session_state.points_df, (gpd.GeoDataFrame)):
        points_in_polygon = gpd.sjoin(left_df=st.session_state.points_df,
                                      right_df=profileUTMBuffer,
                                      how="inner",
                                      predicate="intersects")

def profile_type_update():
    if st.session_state.profile_type == 'Selection':
        st.session_state.profile_crs = DEFAULT_POINTS_CRS
        print("UPDATED", st.session_state.profile_crs)

@st.cache_data
def get_elevation(coords=None,
                  elevation_col_name='elevation',
                  xcoord_col_name='xcoord', ycoord_col_name='ycoord',
                  points_crs=None, output_crs=None,
                  elevation_source=None, elev_source_type='service',
                  raster_crs=None, show_plot=True):

    """This function takes coordinates, specified raster (services), and specified output CRS
       and extracts the elevation values from the raster at the specified coordinates.
       As a streamlit app, this also plots the raster data around the point(s) and the points,
       then displays the points and relevant information in a dataframe that can be copy/pasted.
       
       This was adapted from a standalone python script, so there are inputs/parameters that may
       not be relevant in the streamlit app.
    """
    coords = st.session_state.coords
    elevation_col_name = "ELEVATION"
    xcoord_col_name = st.session_state.xcoord_col
    ycoord_col_name = st.session_state.ycoord_col
    points_crs = st.session_state.well_input_crs
    output_crs = st.session_state.project_crs
    elev_source_type = 'service'
    raster_crs = None

    if coords is None:
        coordType = st.session_state.coordinate_type
        if coordType == "Single":
            coords = (st.session_state.xcoord, st.session_state.ycoord)
        elif coordType == 'Multiple' or coordType == 'Upload':
            coords = st.session_state.point_table
        #if st.session_state.points_source=='Enter coords.':
            #coords = (-88.857362, 42.25637743)

    # Get the correct/specified raster source
    if "ISGS" in st.session_state.elev_source:
        elevation_source = IL_LIDAR_URL
    else:
        elevation_source = GMRT_BASE_URL

    # Get CRS of points, raster, and specified output
    if points_crs is None:
        points_crs = int(CRS_DICT[st.session_state.point_crs].code)
        points_crs_name = CRS_DICT[st.session_state.point_crs].name

    if raster_crs is None:
        raster_crs = int(CRS_DICT[st.session_state.raster_crs].code)
        raster_crs_name = CRS_DICT[st.session_state.raster_crs].name

    if output_crs is None:
        output_crs = int(CRS_DICT[st.session_state.output_crs].code)
        output_crs_name = CRS_DICT[st.session_state.output_crs].name

    # Project coordinates into CRS of raster and specified output CRS
    ptCoordTransformerOUT = pyproj.Transformer.from_crs(crs_from=points_crs,
                                                        crs_to=output_crs,
                                                        always_xy=True)
    ptCoordTransformerRaster = pyproj.Transformer.from_crs(crs_from=points_crs,
                                                           crs_to=raster_crs,
                                                           always_xy=True)

    # Access (geo)dataframe to get location info
    xcoord = coords[xcoord_col_name]
    ycoord = coords[ycoord_col_name]

    xcoord_OUT, ycoord_OUT = ptCoordTransformerOUT.transform(xcoord, ycoord)
    xcoord_RAST, ycoord_RAST = ptCoordTransformerRaster.transform(xcoord, ycoord)

    minXRast = min(xcoord_RAST)
    maxXRast = max(xcoord_RAST)
    minYRast = min(ycoord_RAST)
    maxYRast = max(ycoord_RAST)

    cols = [f"{points_crs}_xIN", f"{points_crs}_yIN", f"{output_crs}_x", f"{output_crs}_y"]
    dfList = []
    for i, xcoordi in enumerate(xcoord):
        dfList.append([xcoord[i], ycoord[i], xcoord_OUT[i], ycoord_OUT[i]])
    coords = pd.DataFrame(dfList, columns=cols)

    # Get padding for visualization purposes
    xPad = (maxXRast-minXRast)*0.1
    yPad = (maxYRast-minYRast)*0.1

    if float(xPad) == 0.0:
        xPad = maxXRast * 0.01
        xPad = abs(xPad)
        if abs(xPad) > 7500:
            xPad = 7500

    if float(yPad) == 0.0:
        yPad = maxYRast * 0.01
        yPad = abs(yPad)
        if abs(yPad) > 7500:
            yPad = 7500

    rasterXMin = minXRast-xPad
    rasterXMax = maxXRast+xPad

    rasterYMin = minYRast-yPad
    rasterYMax = maxYRast+yPad

    # Read in data from service or file, as appropriate
    # ISGS lidar is from the statewide WGS84 service, read in as an (rio)xarray DataSet
    if "Illinois" in st.session_state.elev_source:
        wms = WebMapService(elevation_source)

        bbox = (rasterXMin, rasterYMin, rasterXMax, rasterYMax)

        img = wms.getmap(
            layers=['IL_Statewide_Lidar_DEM_WGS:None'],
            srs='EPSG:3857',
            bbox=bbox,
            size=(256, 256),
            format='image/tiff',
            transparent=True
            )

        bio = BytesIO(img.read())
        elevData_rxr = rxr.open_rasterio(bio)
        elevData_ft = elevData_rxr.rio.reproject(output_crs)
        elevData_m = elevData_ft * 0.3048
    else:
        # GMRT_URL = r"https://www.gmrt.org:443/services/GridServer?minlongitude=-88.4&maxlongitude=-88.2%2C%20&minlatitude=40.1&maxlatitude=40.3&format=geotiff&resolution=default&layer=topo"
        GMRT_URL = GMRT_BASE_URL.replace('minlongitude', f"minlongitude={rasterXMin:0.4f}")
        GMRT_URL = GMRT_URL.replace('maxlongitude', f"maxlongitude={rasterXMax:0.4f}")
        GMRT_URL = GMRT_URL.replace('minlatitude', f"minlatitude={rasterYMin:0.4f}")
        GMRT_URL = GMRT_URL.replace('maxlatitude', f"maxlatitude={rasterYMax:0.4f}")

        response = requests.get(url=GMRT_URL)
        with BytesIO(response.content) as f:
            elevData_rxr = rxr.open_rasterio(f)
        elevData_m = elevData_rxr.rio.reproject(output_crs)

        if 'band' in elevData_m.dims:
            elevData_m = elevData_m.isel(band=0)

        elevData_ft = elevData_m / 0.3048

def read_profile():
    sa_gdf = gpd.read_file()

    st.session_state.profile = sa_gdf

def get_utm_crs(point_geometry):
    from pyproj.database import query_utm_crs_info
    from pyproj.aoi import AreaOfInterest

    # Example: coordinates for Chicago, IL
    lon = point_geometry.x
    lat = point_geometry.y
    utm_crs_list = query_utm_crs_info(
        datum_name="WGS 84",
        area_of_interest=AreaOfInterest(
            west_lon_degree=lon,
            south_lat_degree=lat,
            east_lon_degree=lon,
            north_lat_degree=lat,
        ),
        )

    print()

    # The first result is the best match
    utm_crs = utm_crs_list[0]
    st.session_state.utm_crs_code = utm_crs.code
    return utm_crs.code

def ingest_profile():
    profileBytes = st.session_state.profile_uploader.getvalue()
    fileGDF = gpd.read_file(BytesIO(profileBytes))
    st.session_state.profile_from_file = fileGDF.iloc[0].geometry
    st.session_state.profile_gdf = fileGDF
    gdfcrs = fileGDF.crs
    st.session_state.profile_crs = f"{gdfcrs.auth_name}:{gdfcrs.code} - {gdfcrs.name}"

@st.cache_data
def ingest_table():
    if st.session_state.well_source == 'Table upload':
        # Can be used wherever a "file-like" object is accepted:
        profileBytes = st.session_state.well_uploader.getvalue()
        wellDF = pd.read_csv(BytesIO(profileBytes))

    else:
        server = "datastorm.prairie.illinois.edu"
        database = "GEOPROD"
        driver = "ODBC Driver 17 for SQL Server" # Adjust based on your installed driver version
        connection_url = f"mssql+pyodbc://@{server}/{database}?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes"
        engine = create_engine(connection_url)

        boreholeLoc_df = pd.read_sql_table(table_name='Borehole_Locations',
                                           con=engine,
                                           schema='ISGS2',
                                           columns=['API10', 'LONGITUDE', "LATITUDE"])

        downhole_df = pd.read_sql_table(table_name='vw_downhole_data',
                                        con=engine,
                                        schema='ISGS2',
                                        columns=['api10', 'formation', "top", "bottom"]
                                        )

        wellDF = pd.merge(left=downhole_df,
                          right=boreholeLoc_df,
                          how='inner',
                          left_on='api10',
                          right_on='API10')
        wellDF.drop('api10', axis=1, inplace=True)
        wellDF = wellDF[['API10', 'LONGITUDE', 'LATITUDE', 'formation', 'top', 'bottom']]
        wellDF.columns = wellDF.columns.str.upper()

    st.session_state.well_df_IN = wellDF
    st.session_state.well_columns = wellDF.columns
    return wellDF

    #blocsdf = boreholeLoc_df[['API10', 'LONGITUDE', "LATITUDE"]]
    #downholeLocsDF = pd.merge(left=downhole_df, right=blocsdf, how='inner',left_on='api10', right_on='API10')
    #pvCountyList = ['Whiteside', 'Bureau', "Henry", "Lee", "Rock Island"]
    #pvDF = downholeLocsDF[downholeLocsDF['CNTYNAME'].isin(pvCountyList)]
    #pvDF = pvDF[pvDF["LONGITUDE"] > -91.5]
    #pvDF = pvDF[pvDF["LONGITUDE"] < -88.5]
    #pvDF = pvDF[pvDF["LATITUDE"] > 41]
    #pvDF = pvDF[pvDF["LATITUDE"] < 42]
    #pvDF.plot(x='LONGITUDE', y="LATITUDE", kind='scatter', s=0.1, c='k')


if __name__ == "__main__":
    main()
