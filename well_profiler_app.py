from io import BytesIO
import pathlib
import traceback

from streamlit_folium import st_folium
from folium.plugins import Draw
import folium

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from owslib.wms import WebMapService
import pandas as pd
import plotly
import plotly.express as px
import plotly.graph_objects as go
import pyproj
import requests
import rioxarray as rxr
from shapely.geometry import LineString
from sqlalchemy import create_engine
import streamlit as st
import w4h
import xarray as xr

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


COLORMAPDICT = {'CLAY':'blue', 
                'BEDROCK':'purple', 
                'SAND':'gold', 
                'GRAVEL':'darkgoldenrod', 
                'SOIL':'black', 
                'SAND AND GRAVEL':'darkorange', 
                'UNKNOWN':"rgba(0,0,0,0)", 
                'CLAY AND SAND MIX':'greenyellow', 
                'GENERIC':'gray', 
                'SAND AND CLAY MIX':'greenyellow', 
                'CLAY AND GRAVEL MIX':'olive', 
                'BEDROCK AND OTHER':'lavender', 
                'ORGANIC MATERIAL':'black', 
                'CLAY WITH SAND SEAMS':'greenyellow', 
                'BOULDERY MATERIAL':'indigo', 
                'FILL':'gray', 
                'SILT':'lawngreen', 
                'GRAVEL AND CLAY MIX':'olive', 
                'SAND WITH CLAY SEAMS':'greenyellow', 
                'BOULDER MATERIAL':'indigo', 
                'CLAY WITH GRAVEL SEAMS':'olive', 
                'GRAVEL WITH CLAY SEAMS':'olive'}
COLORDF = pd.DataFrame(COLORMAPDICT, index=['Color']).T.reset_index()
COLORDF.columns = ['INTERPRETED', "PLOTCOLOR"]

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
        st.session_state.mapContainer = st.container()
        
        wellincol, st.session_state.mapCol = wellTab.columns([0.5, 0.5])

        # Profile section
        with st.sidebar:
            st.header('Specify Profile', divider='rainbow')
            pillCol, uploadCol, pCRSCol = st.columns([0.2, 0.6, 0.2],)
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
            bSizeCol, bUnitCol, bEmptyCol = st.columns([0.2, 0.2, 0.6])
            bSizeCol.number_input("Buffer size", min_value=0.0, format="%0.1f",
                                value=1.0,
                                key='buffer_size')
            bUnitCol.selectbox('Buffer Size Unit',
                            options=['feet', 'meters', 'kilometers', 'miles'],
                            index=2, key='buffer_unit')

            # Well Data section
            st.header('Specify Well Data', divider='rainbow')
            well_source = st.pills("Well Data Source",
                            options=['Table upload', 'Database'],
                            default='Database',
                            key='well_source')
            
            if st.session_state.well_source == 'Table upload':
                st.file_uploader("Upload Well Data",
                                    key='well_uploader', on_change=ingest_table)
            else:
                st.session_state.wellGDF = wellGDF = ingest_table(well_source)
                print("TTYPES", type(st.session_state.wellGDF), type(wellGDF))
                if st.session_state.wellGDF is not None:
                    st.session_state.tableTab.dataframe(st.session_state.wellGDF.to_arrow(geometry_encoding='geoarrow'))
                else:
                    st.session_state.tableTab.write("Point table not read in correctly")
            xcol, ycol, crscol = st.columns([0.3, 0.3, 0.6])

            colDisabled = False
            if not hasattr(st.session_state, "well_columns"):
                st.session_state.well_columns = []
                colDisabled = True
            
            xcol.selectbox('X Coordinate Column', key='xcoord_col',
                        options=st.session_state.well_columns,
                        disabled=colDisabled)
            ycol.selectbox('Y Coordinate Column', key='ycoord_col',
                        options=st.session_state.well_columns,
                        disabled=colDisabled)

            crscol.selectbox(label="Well Coordinates CRS", options=CRS_STR_LIST,
                            index=DEFAULT_POINTS_CRS_INDEX,
                            key='well_input_crs')
            st.toggle('Plot Well Points',
                            value=False,
                            key='plot_points_toggle',
                            disabled=colDisabled,
                            on_change=plot_well_points)
            
            # Elevation input
            st.header('Elevation', divider='rainbow')
            eSourceCol, eSpecCol = st.columns([0.4, 0.6],
                                                    vertical_alignment='bottom')
            eSourceCol.pills('Elevation Source',
                            options=["Input table",
                                    "Global dataset (GMRT)",
                                    "Illinois lidar"],
                            default='Illinois lidar', key='elev_source')
            eSource = st.session_state.elev_source
            if eSource == 'Illinois lidar':
                eSpecCol.write('Using ISGS statewide lidar dataset.')
                st.session_state.raster_crs = "EPSG:3857 - WGS 84 / Pseudo-Mercator"
            elif eSource == 'Input table':
                eColOpts = []
                if hasattr(st.session_state, 'well_columns'):
                    eColOpts = st.session_state.well_columns
                eSpecCol.selectbox("Specify Elevation Column",
                                options=eColOpts)
            else:
                eSpecCol.write('Using Global Multiresolution Topography dataset.')

        with st.session_state.mapContainer:
            if "current_profile" not in st.session_state:
                st.session_state.current_profile = None
            if "new_map_draw" not in st.session_state:
                st.session_state.new_map_draw = False
            if 'map_result' not in st.session_state:
                st.session_state.map_result = {'last_active_drawing': None}

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
    lidarUrl = IL_LIDAR_URL.split('?request')[0]
    folium.WmsTileLayer(
        url=lidarUrl,
        name="IL Statewide LiDAR DEM",
        layers="None",
        styles="",
        fmt="image/png",
        version="1.3.0",
        transparent=True,
        attr="Illinois State Geological Survey",
        overlay=True,
        control=True,
        show=False,
    ).add_to(m)

    if hasattr(st.session_state, 'profile_buffer') and isinstance(st.session_state.profile_buffer, gpd.GeoDataFrame):
        bpDF = st.session_state.buffer_points.copy()
        uniqueWells = np.unique(bpDF['API10'])
        plotDFList = []
        geomList = []
        for well in uniqueWells:
            uWellDF = bpDF[bpDF['API10']==well]
            uWellX = uWellDF['LONGITUDE'].iloc[0]
            uWellY = uWellDF['LATITUDE'].iloc[0]
            uWellDepth = str(max(uWellDF['BOTTOM'])) + "'"
            #uWellBottomElev = str(round(min(uWellDF['BOTTOM_ELEV']), 2)) + "' asl"
            geomList.append(uWellDF.geometry.iloc[0])
            plotDFList.append([well, uWellX, uWellY, uWellDepth])
        colList = ['API10', 'LONGITUDE', "LATITUDE", "DEPTH"]
        folium.GeoJson(
            gpd.GeoDataFrame(plotDFList, columns=colList, geometry=geomList, crs=4269),
            name='Selected Wells',
            style_function=lambda x: {
                "color": "black",
                "weight": 0,
                "interactive": True,
                "fillOpacity": 0.85,
            },

            marker=folium.CircleMarker(
                radius=2,
                color='black',
                fill=True,
                fill_color='black',
            ),

            tooltip=folium.GeoJsonTooltip(
                fields=["API10", "LONGITUDE", "LATITUDE", 'DEPTH'],
                aliases=["Well:", "Longitude:", "Latitude:", 'Total Depth:'],
                style=(
                    "background-color: white; "
                    "border: 1px solid black; "
                    "border-radius: 3px; "
                    "box-shadow: 3px;"
                ),
                localize=True,
                sticky=False
            )
        ).add_to(m)

        profile_group = folium.FeatureGroup(name="Current Profile", show=True)
        folium.GeoJson(
            st.session_state.profile_buffer,
            name="Profile Buffer",
            style_function=lambda x: {
                "fillColor": "black",
                "color": "black",
                "weight": 0, 'interactive': False,
                "fillOpacity": 0.1,
            }
        ).add_to(profile_group)

        folium.PolyLine(
            locations=[(lon, lat) for lon, lat in st.session_state.current_profile.coords],
            name="Current Profile",
            color='black', 
            interactive=True,
            weight=3,
            control=True,
            show=True,
        ).add_to(profile_group)
        profile_group.add_to(m)
        bounds = st.session_state.profile_buffer.total_bounds

        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]],
                     padding=[50, 50])

    wellPlotCond = hasattr(st.session_state, 'do_plot_wells') and st.session_state.do_plot_wells
    if wellPlotCond:
        if hasattr(st.session_state, 'buffer_points') and st.session_state.buffer_points is not None:
            #folium.Marker().add_to(m)
            print("PLOTTING WELLS!")
            df = st.session_state.buffer_points.drop(columns=['geometry'])
            df.dropna(subset=["LATITUDE", "LONGITUDE"], inplace=True)
            print("PLOTTING ", df.shape[0], ' points')
            for row in df.itertuples():
                folium.CircleMarker(
                    location=[row.LATITUDE, row.LONGITUDE],
                    radius=1.5, fillOpacity=1, fillColor='blue',
                    weight=0, interactive=False,
                    hover=True,
                    overlay=False, tooltip=row.API10,
                ).add_to(m)
            print('done')
        elif hasattr(st.session_state, 'wellGDF') and st.session_state.wellGDF is not None:
            print("plotting all welsl")
            df = st.session_state.wellGDF.drop(columns=['geometry'])
            df.dropna(subset=["LATITUDE", "LONGITUDE"], inplace=True)
            indices_100 = np.arange(0, df.shape[0], 100)
            dftoPlot = df.iloc[indices_100]
            for row in dftoPlot.itertuples():
                folium.CircleMarker(
                    location=[row.LATITUDE, row.LONGITUDE],
                    radius=1.5, fillOpacity=1, fillColor='blue',
                    weight=0, interactive=False,
                    hover=True,
                    overlay=False, tooltip=row.API10,
                ).add_to(m)
            print('done')

    # Plot profile!!
    if hasattr(st.session_state, "buffer_points"):
        df = st.session_state.buffer_points
        if not hasattr(st.session_state, "elevation_data"):
            st.session_state.elevation_data = get_elevation(st.session_state.elev_source)

        if df is not None and "ELEVATION" not in df.columns:
            if not hasattr(st.session_state, 'elevation_data'):
                #print('We dont even exist!')
                st.session_state.elevation_data = get_elevation(st.session_state.elev_source)
            elif st.session_state.elevation_data is None:
                #print("NONE FIRST")
                st.session_state.elevation_data = get_elevation(st.session_state.elev_source)
                elevData = get_elevation(st.session_state.elev_source)
                #print('We noned')
                #print("LOCAL elevData", type(elevData))
                #print("STSS elevation_data", type(st.session_state.elevation_data))
            else:
                pass
                #print('We elsed')
                #print(type(st.session_state.elevation_data))
                #print(st.session_state.elevation_data)
            xs = xr.DataArray(df["LONGITUDE"].values, dims="points")
            ys = xr.DataArray(df["LATITUDE"].values, dims="points")

            st.session_state.elevation_data[0].plot()
            zData = st.session_state.elevation_data.sel(x=xs,
                                                        y=ys,
                                                        method="nearest").values
            df['SURFACE_ELEVATION'] = zData
            
        if df is not None:
            df["BOTTOM_ELEV"] = df['SURFACE_ELEVATION'] - df["BOTTOM"]
            df["TOP_ELEV"] = df['SURFACE_ELEVATION'] - df["TOP"]

            plot_well_profile()
        
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
    with st.session_state.mapContainer:
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

    if hasattr(st.session_state, "profile_buffer") and hasattr(st.session_state, "wellGDF"):
        st.session_state.buffer_points = gpd.sjoin(left_df=st.session_state.wellGDF,
                                                right_df=st.session_state.profile_buffer,
                                                how="inner",
                                                predicate="intersects")
        
        if hasattr(st.session_state, 'elevation_data'):
            uniqueWells = np.unique(st.session_state.buffer_points['API10'])
            # Potential update: use xarray indexing to sample data values
            xs = xr.DataArray(st.session_state.buffer_points["LONGITUDE"].values, dims="points")
            ys = xr.DataArray(st.session_state.buffer_points["LATITUDE"].values, dims="points")

            elevPts = st.session_state.elevation_data.sel(x=xs, y=ys, method="nearest").values
            st.session_state['SURFACE_ELEVATION'] = elevPts


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

    if hasattr(st.session_state, 'wellGDF') and isinstance(st.session_state.wellGDF, (gpd.GeoDataFrame)):
        st.session_state.buffer_points = gpd.sjoin(left_df=st.session_state.wellGDF,
                                      right_df=st.session_state.profile_buffer,
                                      how="inner",
                                      predicate="intersects")

        if hasattr(st.session_state, 'elevation_data'):
            if st.session_state.elevation_data is None:
                st.session_state.elevation_data = get_elevation(st.session_state.elev_source)
            xArr = xr.DataArray(st.session_state.buffer_points['LONGITUDE'].values, dims="points")
            yArr = xr.DataArray(st.session_state.buffer_points["LATITUDE"].values, dims="points")

            st.session_state.buffer_points['SURFACE_ELEVATION'] = st.session_state.elevation_data.sel(x=xArr, y=yArr, method="nearest").values

def profile_type_update():
    if st.session_state.profile_type == 'Selection':
        st.session_state.profile_crs = DEFAULT_POINTS_CRS
        print("UPDATED", st.session_state.profile_crs)

@st.cache_data
def get_elevation(elev_source):

    """This function takes coordinates, specified raster (services), and specified output CRS
       and extracts the elevation values from the raster at the specified coordinates.
       As a streamlit app, this also plots the raster data around the point(s) and the points,
       then displays the points and relevant information in a dataframe that can be copy/pasted.
       
       This was adapted from a standalone python script, so there are inputs/parameters that may
       not be relevant in the streamlit app.
    """
    print("GETTING ELEVATION!!!!!")
    coords=None
    elevation_col_name='elevation'
    xcoord_col_name='xcoord'
    ycoord_col_name='ycoord'
    points_crs=None
    output_crs=None
    elevation_source=None
    elev_source_type='service'
    raster_crs=None
    show_plot=True
    
    #coords = st.session_state.coords
    elevation_col_name = "ELEVATION"
    xcoord_col_name = st.session_state.xcoord_col
    if st.session_state.xcoord_col is None:
        xcoord_col_name = "LONGITUDE"

    ycoord_col_name = st.session_state.ycoord_col
    if ycoord_col_name is None:
        ycoord_col_name = "LATITUDE"

    points_crs = CRS_DICT[st.session_state.well_input_crs].code
    output_crs = CRS_DICT[st.session_state.project_crs].code
    elev_source_type = 'service'
    raster_crs = None
    print("MADE IT TO THIS PART OF ELEVATION GETTING?")
    #if coords is None:
    #    coordType = st.session_state.coordinate_type
    #    if coordType == "Single":
    #        coords = (st.session_state.xcoord, st.session_state.ycoord)
    #    elif coordType == 'Multiple' or coordType == 'Upload':
    #        coords = st.session_state.point_table
        #if st.session_state.points_source=='Enter coords.':
            #coords = (-88.857362, 42.25637743)

    # Get the correct/specified raster source
    print("CHECKING ESOURCE", hasattr(st.session_state, "elev_source"))
    if "Illinois" in st.session_state.elev_source:
        elevation_source = IL_LIDAR_URL
    else:
        elevation_source = GMRT_BASE_URL

    print("ESOURCE", elevation_source)
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
    elif type(output_crs) is str and output_crs.isnumeric():
        output_crs = int(output_crs)

    # Project coordinates into CRS of raster and specified output CRS
    ptCoordTransformerOUT = pyproj.Transformer.from_crs(crs_from=points_crs,
                                                        crs_to=output_crs,
                                                        always_xy=True)
    ptCoordTransformerRaster = pyproj.Transformer.from_crs(crs_from=points_crs,
                                                           crs_to=raster_crs,
                                                           always_xy=True)

    # Access (geo)dataframe to get location info
    print("DO WE HAVE BUFFER POITNS????", hasattr(st.session_state, 'buffer_points'))
    if hasattr(st.session_state, 'buffer_points'):
        print(type(st.session_state.buffer_points))

    if hasattr(st.session_state, 'buffer_points') and st.session_state.buffer_points is not None:
        # if hasattr(st.session_state, 'well_df_IN') and st.session_state.well_df_IN is not None:
        print("\t yes to buffer points")
        df = st.session_state.buffer_points
        
        coords = df
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
            dfList.append([xcoordi, ycoord.iloc[i], xcoord_OUT[i], ycoord_OUT[i]])
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
            print("READING FROM ILLINOIS DATA WMS")
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
            elevData_rxr = rxr.open_rasterio(bio)[0]
            elevData_ft = elevData_rxr.rio.reproject(output_crs)
            elevData_m = elevData_ft * 0.3048
            st.session_state.elevation_data = elevData_m
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
            st.session_state.elevation_data = elevData_m

        print("ELEVDATA GOT IT")
        elevData_m[0].plot()
        return elevData_m

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

    # The first result is the best match
    utm_crs = utm_crs_list[0]
    st.session_state.utm_crs_code = utm_crs.code
    return utm_crs.code



def plot_well_points():
    if st.session_state.plot_points_toggle:
        st.session_state.do_plot_wells = True
    else:
        st.session_state.do_plot_wells = False


def ingest_profile():
    profileBytes = st.session_state.profile_uploader.getvalue()
    fileGDF = gpd.read_file(BytesIO(profileBytes))
    st.session_state.profile_from_file = fileGDF.iloc[0].geometry
    st.session_state.profile_gdf = fileGDF
    gdfcrs = fileGDF.crs
    st.session_state.profile_crs = f"{gdfcrs.auth_name}:{gdfcrs.code} - {gdfcrs.name}"

@st.cache_data
def ingest_table(well_source):
    print("INGESTING TABLE")
    if st.session_state.well_source == 'Table upload':
        # Can be used wherever a "file-like" object is accepted:
        profileBytes = st.session_state.well_uploader.getvalue()
        wellDF = pd.read_csv(BytesIO(profileBytes))
        wellDF.columns = wellDF.columns.str.upper()
        well_input_crs = st.session_state.well_input_crs
        xcoord_col = st.session_state.xcoord_col
        ycoord_col = st.session_state.ycoord_col
    else:
        try:
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
            wellDF.dropna(subset=["LATITUDE", "LONGITUDE"], inplace=True)
            wellDF = wellDF[['API10', 'LONGITUDE', 'LATITUDE', 'formation', 'top', 'bottom']]
            wellDF.columns = wellDF.columns.str.upper()
            well_input_crs = 'EPSG:4269 - NAD83'
            xcoord_col = "LONGITUDE"
            ycoord_col = "LATITUDE"
        except Exception:
            wellDF = pd.DataFrame()

            traceback.print_exc()

    #st.session_state.well_df_IN = wellDF
    #st.session_state.well_columns = wellDF.columns
    #print("WELLDFIN", type(st.session_state.well_df_IN))
    #print("WELLINCRS", type(well_input_crs))
    #print("XXXCRD", type(st.session_state.xcoord_col))
    #print("YYYYYYYYYXXXCRD" ,type(st.session_state.ycoord_col))
    try:
        geocol = gpd.points_from_xy(wellDF[xcoord_col],
                                    wellDF[ycoord_col])

        gdf = gpd.GeoDataFrame(wellDF,
                               geometry=geocol,
                               crs=CRS_DICT[well_input_crs].code).to_crs(4326)
    except Exception:
        traceback.print_exc()
        gdf = None
        
    return gdf

#def 
#    possibleXCols = ['longitude', 'lon', 'x', 'xcoord', 'east', 'easting', 'utme']
#    possibleYCols = ['latitude', 'lat', 'y', 'ycoord', 'north', 'northing', 'utmn']
#    xColIndex = 0
#    yColIndex = 0

#    for colname in st.session_state.well_columns:
#        xColCond1 = colname.lower() in possibleXCols
#        xColCond2 = str(colname).lower().startswith('x')
#        if xColCond1 or xColCond2:
#            xColIndex = colname
#            continue

#        yColCond1 = colname.lower() in possibleYCols
#        yColCond2 = str(colname).lower().startswith('y')
#        if yColCond1 or yColCond2:
#            yColIndex = colname
#            continue

#    st.session_state.xcoord_col = xColIndex
#    st.session_state.ycoord_col = yColIndex

#    try:
#        geocol = gpd.points_from_xy(wellDF[st.session_state.xcoord_col],
#                                    wellDF[st.session_state.ycoord_col])

#        gdf = gpd.GeoDataFrame(wellDF,
#                               geometry=geocol,
#                               crs=CRS_DICT[st.session_state.well_input_crs].code)
#    except Exception:
#        traceback.print_exc()
#        gdf = None

#    if gdf is not None:
#        st.session_state.wellGDF = gdf.to_crs('EPSG:4326')

#        if hasattr(st.session_state, "profile_buffer"):
#            st.session_state.buffer_points = gpd.sjoin(left_df=gdf,
#                                                       right_df=st.session_state.profile_buffer,
#                                                       how="inner",
#                                                       predicate="intersects")

   #     if hasattr(st.session_state, 'elevation_data'):
  #          xArr = xr.DataArray(st.session_state.buffer_points['LONGITUDE'].values, dims="points")
 #           yArr = xr.DataArray(st.session_state.buffer_points["LATITUDE"].values, dims="points")
#
#            st.session_state.buffer_points['SURFACE_ELEVATION'] = st.session_state.elevation_data.sel(x=xArr, y=yArr, method="nearest").values
#    else:
#        st.session_state.buffer_points = None

    #st.session_state.elevation_data = get_elevation()
    
    #return gdf

    #blocsdf = boreholeLoc_df[['API10', 'LONGITUDE', "LATITUDE"]]
    #downholeLocsDF = pd.merge(left=downhole_df, right=blocsdf, how='inner',left_on='api10', right_on='API10')
    #pvCountyList = ['Whiteside', 'Bureau', "Henry", "Lee", "Rock Island"]
    #pvDF = downholeLocsDF[downholeLocsDF['CNTYNAME'].isin(pvCountyList)]
    #pvDF = pvDF[pvDF["LONGITUDE"] > -91.5]
    #pvDF = pvDF[pvDF["LONGITUDE"] < -88.5]
    #pvDF = pvDF[pvDF["LATITUDE"] > 41]
    #pvDF = pvDF[pvDF["LATITUDE"] < 42]
    #pvDF.plot(x='LONGITUDE', y="LATITUDE", kind='scatter', s=0.1, c='k')


def plot_well_profile():
    if hasattr(st.session_state, 'buffer_points') and 'SURFACE_ELEVATION' in st.session_state.buffer_points.columns:
        df = st.session_state.buffer_points.copy()
        termsDF = pd.read_csv(w4h.get_resources()['LithologyDict_Exact'])
        df = w4h.specific_define(df, termsDF)
        well_id_list = np.unique(st.session_state.buffer_points["API10"])
        minElev = df["BOTTOM_ELEV"].quantile(0.05)
        maxElev = max(df.loc[:, "SURFACE_ELEVATION"])
        df = df.dropna(subset=['API10', "LONGITUDE", "LATITUDE", "FORMATION", "INTERPRETED"])

        print("MINNMAX", minElev, maxElev)
        eRange = abs(maxElev - minElev)
        ePad = eRange * 0.05
        yLIM = [minElev-ePad, maxElev+ePad]

        # Get orientation
        xmin = min(df["LONGITUDE"])
        xmax = max(df["LONGITUDE"])
        xReversed = xmin > xmax
        xRange = abs(xmax - xmin)

        ymin = min(df["LATITUDE"])
        ymax = max(df["LATITUDE"])
        yReversed = ymin > ymax
        yRange = abs(ymax - ymin)

        if yRange > xRange:
            orientation = 'Y'
            distCol = 'LATITUDE'
            isReversed = yReversed
        else:
            orientation = 'X'
            distCol = 'LONGITUDE'
            isReversed = xReversed

        def linetobox(x, y, width_factor=200):
            linewidth = abs(xmax - xmin) / width_factor
            #print(linewidth)
            xlow = x[0]-linewidth
            xhi = x[0]+linewidth

            xReturn = [xlow, xlow, xhi, xhi, xlow]
            yReturn = [y[1], y[0], y[0], y[1], y[1]]
            return xReturn, yReturn


        # Plot data
        legendList = []
        fig = go.Figure()
        for well in well_id_list:
            wellDF = df[df['API10'] == well].reset_index(drop=True)
            #print(wellDF[distCol])

            if len(wellDF[distCol]) > 0:
                minWellElev = min(wellDF["BOTTOM_ELEV"])
                maxWellElev = max(wellDF["TOP_ELEV"])
                wellX = wellDF[distCol].iloc[0]

                fig.add_trace(go.Scatter(
                    x=[wellX, wellX],
                    y=[minWellElev, maxWellElev],
                    #fill="toself",
                    #fillcolor=fColor,
                    line=dict(color='black', width=2, dash="3px 1px"),
                    marker=dict(size=4, line=dict(width=1, color='black'), symbol='line-ew'),
                    mode='markers+lines',
                    hoverinfo='skip',
                    showlegend=False,
                    name=well
                ))
        
        for index, row in df.iterrows():
            #print(row, distCol)
            xarr = [row[distCol], row[distCol]]
            yarr = [row["TOP_ELEV"], row["BOTTOM_ELEV"]]

            xboxarr = xarr
            yboxarr = yarr
            #xboxarr, yboxarr = linetobox(xarr, yarr)
            
            legendName = row['INTERPRETED']
            showLegend = True
            if row['INTERPRETED'] in legendList:
                showLegend = False
                
            hoverText = f"Well: {row['API10']} <br> Description: {row['FORMATION'][:25]} <br> Interpretation: {row['INTERPRETED']}"
            #print("HVRTEXT", hoverText)
            # Define rectangle corners
            try:
                fColor = COLORMAPDICT[row["INTERPRETED"]]
            except Exception:
                fColor = 'black'

            fig.add_trace(go.Scatter(
                x=xboxarr,
                y=yboxarr,
                #fill="toself",
                #fillcolor=fColor,
                line=dict(color=fColor, width=2),
                #linecolor=None,
                mode='lines',
                            
                # Pattern fill
                #fillpattern=dict(fgcolor='yellow',
                #    shape=".",   # "/", "\\", "x", "-", "|", ".", "+"
                #    bgcolor='black',
                #    size=1,),
                #hovertemplate=hoverText,
                #text=hoverText,
                hoverinfo='text',           # Only show custom text
                hovertext=hoverText,
                #hovertemplate=(
                #    "<b>Well:</b> %{customdata[0]}<br>"
                #    "<b>Formation:</b> %{customdata[1]}<br>"
                #    "<b>Interpreted:</b> %{customdata[2]}<br>"
                #    "<extra></extra>"
                #),
                #hovertemplate="%{text}<extra></extra>",
                showlegend=showLegend,
                name=legendName
            ))
            legendList.append(row["INTERPRETED"])
        fig.update_yaxes(range=[minElev, maxElev])
        fig.update_layout(hovermode='closest')

        st.session_state.mapContainer.plotly_chart(fig,
                                             key='well_profile',
                                            width='stretch',
                                            config={'displayModeBar': True})
        print(fig.data[0].hoverinfo)
        print(fig.data[0].text)

if __name__ == "__main__":
    main()
