# You MUST run this with python3
# To Run:  python3 run_flaskserver_zone.py

import signal
import sys
import logging
import time
import os

# check if it's ran with Python3
assert sys.version_info[0:1] == (3,)

# imports needed for web server
from flask import Flask, jsonify, render_template, request, Response, send_from_directory, url_for
from werkzeug.serving import make_server

# imports needed for stream server
import io
import picamera
import socketserver
from threading import Condition, Thread, Event
from http import server

import Adafruit_PCA9685

pwm = Adafruit_PCA9685.PCA9685()
pwm.set_pwm_freq(50)


logging.basicConfig(level = logging.INFO)

# for triggering the shutdown procedure when a signal is detected
keyboard_trigger = Event()
def signal_handler(signal, frame):
    logging.info('Signal detected. Stopping threads.')
    keyboard_trigger.set()

#######################
### Web Server Stuff ##
#######################

# Directory Path can change depending on where you install this file.  Non-standard installations
# may require you to change this directory.
directory_path = 'static'

HOST = "0.0.0.0"
WEB_PORT = 5000
app = Flask(__name__, static_url_path='')

class WebServerThread(Thread):
    '''
    Class to make the launch of the flask server non-blocking.
    Also adds shutdown functionality to it.
    '''
    def __init__(self, app, host, port):
        Thread.__init__(self)
        self.srv = make_server(host, port, app)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        logging.info('Starting Flask server')
        self.srv.serve_forever()

    def shutdown(self):
        logging.info('Stopping Flask server')
        self.srv.shutdown()

class Position(object):
    def __init__(self):
        self.x = 0
        self.y = 0

pos = Position()

@app.route("/cmd", methods = ["POST"])
def commands():
    # get the query
    args = request.args
    x_range = 350
    y_range = 150
    x_min = 200
    y_min = 250
    
    pos.x = int(x_range - (float(args['x']) * x_range) + x_min)
    pos.y = int(y_range - (float(args['y']) * y_range) + y_min)
    
    ###############################################
    #controls
    pwm.set_pwm(0, 0, pos.x)
    pwm.set_pwm(1, 0, pos.y)

    ###############################################

    resp = Response()
    resp.mimetype = "application/json"
    resp.status = "OK"
    resp.status_code = 200
    return resp

@app.route("/")
def index():
    return page("index.html")

@app.route("/<string:page_name>")
def page(page_name):
    return render_template("{}".format(page_name))

@app.route("/static/<path:path>")
def send_static(path):
    return send_from_directory(directory_path, path)

#############################
### Video Streaming Stuff ###
#############################

class StreamingOutput(object):
    '''
    Class to which the video output is written to.
    The buffer of this class is then read by StreamingHandler continuously.
    '''
    def __init__(self):
        self.frame = None
        self.buffer = io.BytesIO()
        self.condition = Condition()

    def write(self, buf):
        if buf.startswith(b'\xff\xd8'):
            # New frame, copy the existing buffer's content and notify all
            # clients it's available
            self.buffer.truncate()
            with self.condition:
                self.frame = self.buffer.getvalue()
                self.condition.notify_all()
            self.buffer.seek(0)
        return self.buffer.write(buf)

class StreamingHandler(server.BaseHTTPRequestHandler):
    '''
    Implementing GET request for the video stream.
    '''
    def do_GET(self):
        if self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                stamp = time.time()
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning(
                    'Removed streaming client %s: %s',
                    self.client_address, str(e))
        else:
            self.send_error(404)
            self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

#############################
### Aggregating all calls ###
#############################

RESOLUTION = [320, 240]
FPS = 30

if __name__ == "__main__":
    # registering both types of signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # firing up the video camera (pi camera)
    camera = picamera.PiCamera(resolution=str(RESOLUTION[0]) + "x" + str(RESOLUTION[1]),
                               framerate=FPS)
    camera.rotation = 180
    output = StreamingOutput()
    camera.start_recording(output, format='mjpeg')
    logging.info("Started recording with picamera")
    STREAM_PORT = 5001
    stream = StreamingServer((HOST, STREAM_PORT), StreamingHandler)

    # starting the video streaming server
    streamserver = Thread(target = stream.serve_forever)
    streamserver.start()
    logging.info("Started stream server for picamera")

    # starting the web server
    webserver = WebServerThread(app, HOST, WEB_PORT)
    webserver.start()
    logging.info("Started Flask web server")

    # and run it indefinitely
    while not keyboard_trigger.is_set():
        time.sleep(0.1)

    # until some keyboard event is detected
    logging.info("Keyboard event detected")

    # trigger shutdown procedure
    webserver.shutdown()
    camera.stop_recording()
    stream.shutdown()
    # Stop the machine
    pwm.set_pwm(0, 0, 0)
    pwm.set_pwm(1, 0, 0)


    # and finalize shutting them down
    webserver.join()
    streamserver.join()
    logging.info("Stopped all threads")

    sys.exit(0)