# ---------------- User Configuration Settings for speed-cam.py ---------------------------------
#         Ver 13.11 speed-cam.py plugin rtsp352.py IP cam Stream Config Settings
#######################################
#    speed-cam.py plugin settings
#######################################

CALIBRATE_ON = True
CAL_OBJ_PX_L2R = 80
CAL_OBJ_MM_L2R = 5700
CAL_OBJ_PX_R2L = 85
CAL_OBJ_MM_R2L = 4700
ALIGN_CAM_ON = False
CAMERA = "rtspcam"
RTSPCAM_SRC = "rtsp://user:password@camera-host:554/stream1"

# Camera Image Stream Settings
IM_SIZE = (640, 352)
GUI_WINDOW_ON = False
GUI_IMAGE_WIN_ON = True

# Motion Tracking Window Crop Area Settings
MO_CROP_AUTO_ON = False
MO_CROP_X_LEFT = 200
MO_CROP_X_RIGHT = 450
MO_CROP_Y_UPPER = 155
MO_CROP_Y_LOWER = 225

# Motion Event Settings
MO_TRACK_EVENT_COUNT = 5
MO_MIN_AREA_PX = 500
MO_LOG_OUT_RANGE_ON = True
MO_MAX_X_DIFF_PX = 26
MO_MIN_X_DIFF_PX = 1
MO_X_LR_SIDE_BUFF_PX = 10
MO_TRACK_TIMEOUT_SEC = 0.5
MO_EVENT_TIMEOUT_SEC = 0.3
MO_MAX_SPEED_OVER = 0
MO_MAX_VALID_SPEED = 60.5

# Camera Image Settings
IM_FRAMERATE = 20
IM_SHOW_CROP_AREA_ON = True
IM_SHOW_SPEED_FILENAME_ON = True
IM_SHOW_TEXT_ON = True
IM_SHOW_TEXT_BOTTOM_ON = True
IM_FONT_SIZE_PX = 16
IM_FONT_SCALE = 0.5
IM_FONT_THICKNESS = 2
IM_FONT_COLOR = (255, 255, 255)
IM_BIGGER = 1.8

# ---------------------------------------- End of User Variables ------------------------------------------
