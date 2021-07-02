#!/usr/bin/env python3

import sys
import time
import yaml
import signal
import argparse
import threading
import paho.mqtt.client as mqtt

from sensors import * 


mqttClient = None
WAIT_TIME_SECONDS = 60
deviceName = None

class ProgramKilled(Exception):
    pass

def signal_handler(signum, frame):
    raise ProgramKilled

class Job(threading.Thread):
    def __init__(self, interval, execute, *args, **kwargs):
        threading.Thread.__init__(self)
        self.daemon = False
        self.stopped = threading.Event()
        self.interval = interval
        self.execute = execute
        self.args = args
        self.kwargs = kwargs

    def stop(self):
        self.stopped.set()
        self.join()

    def run(self):
        while not self.stopped.wait(self.interval.total_seconds()):
            self.execute(*self.args, **self.kwargs)

def write_message_to_console(message):
    print(message)
    sys.stdout.flush()

def updateSensors():
    payload_str = f'{{'
    for sensor, attr in sensors.items():
        if 'settings' in attr and settings[attr['settings']] == False:
            continue
        payload_str += f'"{sensor}": "{attr["function"]()}",'
    payload_str = payload_str[:-1]
    payload_str += f'}}'
    print(f'system-sensors/sensor/{deviceName}/state')
    print(payload_str)
    mqttClient.publish(
        topic=f'system-sensors/sensor/{deviceName}/state',
        payload=payload_str,
        qos=1,
        retain=False,
    )

def remove_old_topics():
    for sensor, attr in sensors.items():
        print(f'homeassistant/{attr["sensor_type"]}/{deviceName}/{sensor}/config')
        mqttClient.publish(
            topic=f'homeassistant/{attr["sensor_type"]}/{deviceName}/{sensor}/config',
            payload='',
            qos=1,
            retain=False,
        )

def send_config_message(mqttClient):

    write_message_to_console('send config message')     

    for sensor, attr in sensors.items():
        if 'settings' in attr and settings[attr['settings']] == False:
            continue
        mqttClient.publish(
            topic=f'homeassistant/sensor/{deviceName}/{sensor}/config',
            payload = (f'{{'
                    + (f'"device_class":"{attr["class"]}",' if 'class' in attr else '')
                    + f'"name":"{deviceNameDisplay} {attr["name"]}",'
                    + f'"state_topic":"system-sensors/sensor/{deviceName}/state",'
                    + (f'"unit_of_measurement":"{attr["unit"]}",' if 'unit' in attr else '')
                    + f'"value_template":"{{{{value_json.{sensor}}}}}",'
                    + f'"unique_id":"{deviceName}_sensor_{sensor}",'
                    + f'"availability_topic":"system-sensors/sensor/{deviceName}/availability",'
                    + f'"device":{{"identifiers":["{deviceName}_sensor"],'
                    + f'"name":"{deviceNameDisplay} Sensors","model":"RPI {deviceNameDisplay}", "manufacturer":"RPI"}}'
                    + (f',"icon":"mdi:{attr["icon"]}"' if 'icon' in attr else '')
                    + f'}}'
                    ),
            qos=1,
            retain=True,
        )

    mqttClient.publish(f'system-sensors/sensor/{deviceName}/availability', 'online', retain=True)

def _parser():
    """Generate argument parser"""
    parser = argparse.ArgumentParser()
    parser.add_argument('settings', help='path to the settings file')
    return parser

def set_defaults(settings):
    DEFAULT_TIME_ZONE = pytz.timezone(settings['timezone'])
    WAIT_TIME_SECONDS = settings['update_interval'] if 'update_interval' in settings else 60
    if 'port' not in settings['mqtt']:
        settings['mqtt']['port'] = 1883
    if 'check_available_updates' not in settings:
        settings['check_available_updates'] = False
    if 'check_wifi_ssid' not in settings:
        settings['check_wifi_ssid'] = False
    if 'check_wifi_strength' not in settings:
        settings['check_wifi_strength'] = False
    if 'external_drives' not in settings:
        settings['external_drives'] = {}

def check_settings(settings):
    values_to_check = ['mqtt', 'timezone', 'deviceName', 'client_id']
    for value in values_to_check:
        if value not in settings:
            write_message_to_console('{value} not defined in settings.yaml! Please check the documentation')
            sys.exit()
    if 'hostname' not in settings['mqtt']:
        write_message_to_console('hostname not defined in settings.yaml! Please check the documentation')
        sys.exit()
    if 'user' in settings['mqtt'] and 'password' not in settings['mqtt']:
        write_message_to_console('password not defined in settings.yaml! Please check the documentation')
        sys.exit()
    if 'check_available_updates' in settings and apt_disabled:
        write_message_to_console('Unable to import apt package. Available updates will not be shown.')
        settings['check_available_updates'] = False
    if 'power_integer_state' in settings:
        write_message_to_console('power_integer_state is deprecated please remove this option power state is now a binary_sensor!')

def add_drives():
    for drive in settings['external_drives']:
        sensors[f'disk_use_{drive.lower()}'] = {
                     'name': f'Disk Use {drive}',
                     'unit': '%',
                     'icon': 'harddisk'
                     }

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        write_message_to_console('Connected to broker')
        client.subscribe('hass/status')
        mqttClient.publish(f'system-sensors/sensor/{deviceName}/availability', 'online', retain=True)
    elif rc == 5:
        write_message_to_console('Authentication failed.\n Exiting.')
        sys.exit()
    else:
        write_message_to_console('Connection failed')

def on_message(client, userdata, message):
    print (f'Message received: {message.payload.decode()}'  )
    if(message.payload.decode() == 'online'):
        send_config_message(client)

if __name__ == '__main__':
    args = _parser().parse_args()
    with open(args.settings) as f:
        settings = yaml.safe_load(f)
    set_defaults(settings)
    check_settings(settings)
    
    add_drives()

    deviceName = settings['deviceName'].replace(' ', '').lower()
    deviceNameDisplay = settings['deviceName']

    mqttClient = mqtt.Client(client_id=settings['client_id'])
    mqttClient.on_connect = on_connect                      #attach function to callback
    mqttClient.on_message = on_message
    mqttClient.will_set(f'system-sensors/sensor/{deviceName}/availability', 'offline', retain=True)
    if 'user' in settings['mqtt']:
        mqttClient.username_pw_set(
            settings['mqtt']['user'], settings['mqtt']['password']
        )
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    while True:
        try:
            mqttClient.connect(settings['mqtt']['hostname'], settings['mqtt']['port'])
            break
        except ConnectionRefusedError:
            time.sleep(120)
    try:
        remove_old_topics() # why are we doing this??
        send_config_message(mqttClient)
    except:
        write_message_to_console('something whent wrong') # say what went wrong
    job = Job(interval=dt.timedelta(seconds=WAIT_TIME_SECONDS), execute=updateSensors)
    job.start()

    mqttClient.loop_start()

    while True:
        try:
            sys.stdout.flush()
            time.sleep(1)
        except ProgramKilled:
            write_message_to_console('Program killed: running cleanup code')
            mqttClient.publish(f'system-sensors/sensor/{deviceName}/availability', 'offline', retain=True)
            mqttClient.disconnect()
            mqttClient.loop_stop()
            sys.stdout.flush()
            job.stop()
            break