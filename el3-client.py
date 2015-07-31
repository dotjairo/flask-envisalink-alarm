# author: Jairo Sanchez
# gevent code based on snippet by oskar.blom@gmail.com
# on http://flask.pocoo.org/snippets/116/
# Make sure your gevent version is >= 1.0
import gevent
from gevent.pywsgi import WSGIServer
from gevent.queue import Queue
from flask import Flask, Response
from flask import render_template
import el3client.Envisalink
from el3client.AlarmServerConfig import AlarmServerConfig
import argparse
import json
import logging
import time
import notify
import sparkfun

logger = logging.getLogger('alarmserver')
logger.setLevel(logging.WARNING)
# Console handler
# Prints all messages (debug level)
ch = logging.StreamHandler();
ch.setLevel(logging.WARNING)
# create formatter
formatter = logging.Formatter(
    fmt='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%b %d %H:%M:%S')
ch.setFormatter(formatter);
# add handlers to logger
logger.addHandler(ch)

# globals
EnvisalinkClient = None
CONNECTEDCLIENTS={}

# SSE "protocol" is described here: http://mzl.la/UPFyxY
class ServerSentEvent(object):

    def __init__(self, data):
        self.data = data
        self.event = None
        self.id = None
        self.desc_map = {
            self.data : "data",
            self.event : "event",
            self.id : "id"
        }

    def encode(self):
        if not self.data:
            return ""
        lines = ["%s: %s" % (v, k)
                 for k, v in self.desc_map.iteritems() if k]

        return "%s\n\n" % "\n".join(lines)

app = Flask(__name__)
subscriptions = []

# Client code consumes like this.
@app.route("/")
def index():
    return render_template('index.htm')

@app.route("/debug")
def debug():
    return "Currently %d subscriptions" % len(subscriptions)

def publish():
    # spin forever
    while True:
        msg = str(time.time())
        for sub in subscriptions[:]:
            sub.put(json.dumps(EnvisalinkClient._alarmstate))

        gevent.sleep(1)

@app.route("/subscribe")
def subscribe():
    def gen():
        q = Queue()
        subscriptions.append(q)
        try:
            while True:
                result = q.get()
                ev = ServerSentEvent(str(result))
                yield ev.encode()
        except GeneratorExit: # Or maybe use flask signals
            subscriptions.remove(q)

    return Response(gen(), mimetype="text/event-stream")

@app.route("/api")
def api():
    return Response(json.dumps(EnvisalinkClient._alarmstate))

@app.route("/api/refresh")
def refresh():
    EnvisalinkClient.send_command('001', '')
    return Response(json.dumps({'response' : 'Request to refresh data received'}))

def main():
    global EnvisalinkClient

    parser = argparse.ArgumentParser('Flask powered Alarm Server')
    parser.add_argument('config', help='Configurationf file', default='')
    args = parser.parse_args()

    logger.info('Using configuration file %s' % args.config)

    config = AlarmServerConfig(args.config)

    # pushover
    pushnotify = notify.pushover(config.PUSHOVER_APPTOKEN, config.PUSHOVER_USERTOKEN)

    # sparkfun data
    sf = sparkfun.SparkFun('data.sparkfun.com', config.PHANT_PUBLICKEY, 
            config.PHANT_PRIVATEKEY, config.PHANT_FIELDS)

    def zoneopen(code,zone, name):
        msg = '{} (zone: {})'.format(name, zone)
        sf.updatezone(zone, 'Open')
        sf.publish()
        #pushnotify.send(msg, priority=-1)
        print msg

    def zoneclosed(code, zone, name):
        sf.updatezone(zone, '')

    def partition_cb(code, partition, name):
        if code == 652:
            msg = '{} Armed'.format(name)
            priority = 0
        elif code == 654:
            msg = '{} In Alarm!!!'.format(name)
            priority = 1
        elif code == 655 or code == 750:
            msg = '{} Disarmed'.format(name)
            priority = 0
        elif code == 657:
            msg = '{} Entry Delay'.format(name)
            priority = 1
        pushnotify.send(msg, priority=priority)


    # Create Envisalink client object
    EnvisalinkClient = el3client.Envisalink.Client(config, CONNECTEDCLIENTS)
    # register callbacks
    EnvisalinkClient.register_cb(609, zoneopen)
    EnvisalinkClient.register_cb(610, zoneclosed)
    EnvisalinkClient.register_cb(652, partition_cb) # partition armed
    EnvisalinkClient.register_cb(654, partition_cb) # in alarm!
    EnvisalinkClient.register_cb(655, partition_cb) # alarm disarmed
    EnvisalinkClient.register_cb(750, partition_cb) # partition disarmed

    gevent.spawn(EnvisalinkClient.connect)

    app.debug = True
    server = WSGIServer(("", 5000), app, keyfile=config.KEYFILE, certfile=config.CERTFILE)

    gevent.spawn(publish)
    pushnotify.send('Alarm service started')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print "Crtl+C pressed. Shutting down."
        logger.info('Shutting down from Ctrl+C')

        server.stop()
        raise SystemExit

if __name__ == "__main__":
    main()
