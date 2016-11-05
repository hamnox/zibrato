import unittest
from datetime import datetime
import threading
import random
from time import sleep

import sys
import os

from expecter import expect
import zmq
from zibrato import Zibrato, Librato, Backend, Broker
sys.path.append('..')
sys.path.append(os.path.join(sys.path[0], '..'))


try:
    import config
except ImportError:
    raise ImportError(
        """

        Edit tests/config.py.dist and save as config.py to
        complete these tests.

        """
    )

BROKER_HOST = '127.0.0.1'
PUB_HOST = '127.0.0.1'
SUB_HOST = '127.0.0.1'
PUB_PORT = 5550
SUB_PORT = 5551

CONTEXT = zmq.Context.instance()


class Receiver(object):

    """
    Create a ZeroMQ subscriber. This is what will "hear" Zibrato publish
    messages. Must be done before initializing Zibrato.
    """

    def __init__(self, **kwargs):
        context = kwargs.get('context') or zmq.Context()
        host = kwargs.get('host') or SUB_HOST
        port = kwargs.get('port') or SUB_PORT
        self.context = context
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect('tcp://%s:%d' % (host, int(port)))

    def receive(self, sub):
        if sub:
            self.socket.setsockopt_string(zmq.SUBSCRIBE, sub)
        self.socket.RCVTIMEO = 1000
        try:
            return self.socket.recv()
        except zmq.ZMQError as e:
            return e.strerror

    def close(self):
        self.socket.close()

RECEIVER = Receiver(context=CONTEXT, host=SUB_HOST, port=SUB_PORT)
Z = Zibrato(context=CONTEXT, host=PUB_HOST, port=PUB_PORT)
L = Librato(
    context=CONTEXT, host=SUB_HOST, port=SUB_PORT,
    username=config.librato['username'],
    apitoken=config.librato['apitoken'])
BROKER = Broker(context=CONTEXT, host=PUB_HOST, port=PUB_PORT)
BACKEND = Backend(context=CONTEXT, host=SUB_HOST, port=SUB_PORT)


def set_up_module():
    broker_thread = threading.Thread(target=BROKER.main)
    broker_thread.start()
    BACKEND.subscribe('testing_backend')
    L.subscribe('testing_librato')
    sleep(0.5)


def tear_down_module():
    global RECEIVER, Z, L, BROKER, BACKEND
    RECEIVER.close()
    del RECEIVER
    Z.close()
    del Z
    L.close()
    del L
    BACKEND.close()
    del BACKEND
    CONTEXT.term()


class TestThatZibratoIsAvailable(unittest.TestCase):

    """
    Start with making sure the class is present and acts right for the
    developer.
    """

    def test_starting_zibrato_with_a_specified_socket(self):
        expect(Z.connected()) == True

    def test_starting_zibrato_with_an_invalid__socket(self):
        with expect.raises(zmq.ZMQError):
            z1 = Zibrato(host='nowhere')
            del z1

    def test_starting_zibrato_with_a_default_socket(self):
        z2 = Zibrato()
        expect(Z.connected()) == True
        z2.close()
        del z2

    def test_starting_a_second_instance_on_the_same_socket(self):
        z3 = Zibrato(host=PUB_HOST, port=PUB_PORT)
        expect(Z.connected()) == True
        z3.close()
        del z3


class TestSendingAMessageToZeroMQ(object):

    def test_if_we_queued_a_message(self):
        z_thread = threading.Thread(
            target=Z.send,
            kwargs=({
                'level': 'testing',
                'value': 'test_if_we_queued_a_message'}))
        z_thread.start()
        expect(RECEIVER.receive('testing')[0:49]) == (
            'testing|Gauge|default|test_if_we_queued_a_message')

    def test_that_we_can_fail_to_receive_a_message_with_report(self):
        z_thread = threading.Thread(
            target=Z.send,
            kwargs=(
                {'level': 'failme', 'value': 'test_if_we_queued_a_message'}))
        z_thread.start()
        expect(RECEIVER.receive('testing')
              ) == 'Resource temporarily unavailable'


class TestMetricsAsDecorators(object):

    @Z.count_me(level='info', name='countertest')
    def function_that_will_be_counted(self):
        pass

    def test_counter_as_decorator(self):
        self.function_that_will_be_counted()
        received = RECEIVER.receive('info')
        count = float(received.split('|')[3])
        expect(count) == 1

    @Z.count_me(level='info', name='countertest', value=5)
    def function_that_will_be_counted_plus_five(self):
        pass

    def test_counter_as_decorator_with_larger_increment(self):
        self.function_that_will_be_counted_plus_five()
        received = RECEIVER.receive('info')
        count = float(received.split('|')[3])
        expect(count) == 5

    @Z.time_me(level='info', name='timertest')
    def function_that_takes_some_time(self):
        sleep(0.1)

    def test_timer_as_decorator(self):
        self.function_that_takes_some_time()
        received = RECEIVER.receive('info')
        time = float(received.split('|')[3])
        expect(time) >= 0.100


class TestMetricsAsContextManagers(object):

    def test_counter_as_a_context_manager(self):
        with Z.Count_me(level='info', name='countermanager'):
            pass
        received = RECEIVER.receive('info')
        count = float(received.split('|')[3])
        expect(count) == 1

    def test_counter_plus_five_as_a_context_manager(self):
        with Z.Count_me(level='info', name='countermanager', value=5):
            pass
        received = RECEIVER.receive('info')
        count = float(received.split('|')[3])
        expect(count) == 5

    def test_timer_as_a_context_manager(self):
        with Z.Time_me(level='info', name='timermanager'):
            sleep(0.1)
        received = RECEIVER.receive('info')
        time = float(received.split('|')[3])
        expect(time) >= 0.100


class TestGauges(object):

    def test_gauge_with_value(self):
        Z.gauge(level='testing', name='test_gauge', value=999)
        received = RECEIVER.receive('testing')
        expect(received[0:28]) == 'testing|Gauge|test_gauge|999'


class TestTheBackend(object):

    def test_that_we_can_retrieve_messages_from_the_queue(self):
        Z.gauge(level='testing_backend',
                source='TestTheBackend',
                name='test_that_we_can_retrieve_messages_from_the_queue',
                value=1)
        expect(BACKEND.receive_one()[0:73]) == (
            'testing_backend|Gauge|' +
            'test_that_we_can_retrieve_messages_from_the_queue|1')

    def test_that_we_can_parse_a_message(self):
        message = Z.pack(level='testing_backend',
                         mtype='Counter',
                         source='TestTheBackend',
                         name='test_counter',
                         value=5)
        mtype, parsed = BACKEND.parse(message)
        expect(parsed.name) == 'test_counter'
        expect(parsed.source) == 'TestTheBackend'
        expect(parsed.value) == 5

    def test_that_we_can_post_a_message(self):
        message = Z.pack(level='testing_backend',
                         mtype='Counter',
                         source='TestTheBackend',
                         name='test_counter',
                         value=5)
        BACKEND.post(message)
        resp = BACKEND.queue['counters'][0]._asdict()
        del resp['measure_time']
        expect(resp) == {
            'name': 'test_counter', 'value': 5.0, 'source': 'TestTheBACKEND'}


class TestLibrato(object):

    def test_that_we_can_parse_a_message(self):
        message = Z.pack(level='testing_librato',
                         mtype='Counter',
                         source='TestLibrato',
                         name='test_counter',
                         value=5)
        mtype, parsed = L.parse(message)
        expect(mtype) == 'counters'
        expect(parsed.name) == 'test_counter'
        expect(parsed.source) == 'TestLibrato'
        expect(parsed.value) == 5

    def test_that_we_can_connect_to_librato(self):
        expect(L.connect()) == 200

    def test_that_we_can_send_a_gauge(self):
        message = Z.pack(level='testing_librato',
                         mtype='Gauge',
                         source='TestLibrato',
                         name='test_that_we_can_send_a_gauge',
                         value=random.randrange(999))
        L.post(message)
        resp = L.flush()
        expect(resp) == 200

    def test_that_we_can_send_a_counter_from_zibrato(self):
        with Z.Count_me(level='testing_librato', source='TestLibrato',
                        name='test_that_we_can_send_a_counter_from_zibrato',
                        value=datetime.now().second):
            pass
        L.post(L.receive_one())
        resp = L.flush()
        expect(resp) == 200

    def test_that_we_can_send_a_timer_from_zibrato(self):
        with Z.Time_me(
            level='testing_librato',
            source='TestLibrato',
            name='test_that_we_can_send_a_timer_from_zibrato'
        ):
            sleep(0.01)
        L.post(L.receive_one())
        resp = L.flush()
        expect(resp) == 200

    def test_that_we_can_send_multiple_metrics(self):
        for x in range(2):
            message = Z.pack(level='testing_librato',
                             mtype='Gauge',
                             source='TestLibrato' + str(x),
                             name='test_that_we_can_send_multiple_metrics.gauge',
                             value=random.randrange(100))
            L.post(message)
        for x in range(random.randrange(10)):
            message = Z.pack(level='testing_librato',
                             mtype='Counter',
                             source='TestLibrato',
                             name='test_that_we_can_send_multiple_metrics.counter',
                             value=1)
            L.post(message)
        resp = L.flush()
        expect(resp) == 200

    def test_that_we_can_roll_up_counters(self):
        for x in range(random.randrange(20)):
            message = Z.pack(level='testing_librato',
                             mtype='Counter',
                             source='TestLibrato',
                             name='test_that_we_can_send_roll_up_counters',
                             value=x)
            L.post(message)
        L.rollup_counters()
        expect('counters' in L.queue) == False
