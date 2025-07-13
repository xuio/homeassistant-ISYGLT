DOMAIN = "isyglt"

CONF_HUB = "hub"
CONF_DEVICES = "devices"
CONF_TYPE = "type"
CONF_ADDRESS = "address"
CONF_CCT_MIN = "cct_min"
CONF_CCT_MAX = "cct_max"
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_POLL_INTERVAL = 1.0
CONF_PRESCALER = "prescaler"

DEVICE_TYPE_RGB_LIGHT = "dmx_rgb"
DEVICE_TYPE_WHITE_LIGHT = "white_light"
DEVICE_TYPE_MOTION_SENSOR = "motion_sensor"
DEVICE_TYPE_BUTTON_GRID = "button_grid"
DEVICE_TYPE_IO_MODULE = "io_module"
DEVICE_TYPE_DIMMER = "dimmer"

ATTR_ZONE = "zone"
ATTR_BUTTON = "button"
ATTR_OUTPUT = "output"
ATTR_INPUT = "input"

LIGHT_REGISTER_COUNT_RGB = 5  # CH0 flags, DIM, R, G, B
LIGHT_REGISTER_COUNT_WHITE = 3  # 3 channels 
LIGHT_REGISTER_COUNT_DIMMER = 2  # CH0, CH1 

CONF_BIT = "bit"

DEVICE_TYPE_GROUP_SWITCH = "group_switch" 