import os
import re
import time
from functools import lru_cache

import yaml
import structlog
from slackclient import SlackClient

class Channel:
    def __init__(self, domain, name, token):
        self.client = SlackClient(token)
        self.name = name
        self.domain = domain

        channels_info = self.client.api_call('channels.list')
        for ci in channels_info['channels']:
            if ci['name'] == name:
                self.id = ci['id']
                break
        else:
            raise ValueError('No channel named %s in slack %s' % (name, domain))

        self.messages = []

        self.client.rtm_connect()

        self.log = structlog.get_logger()
        self.log = self.log.bind(
            domain=domain,
            channel=name
        )


    @lru_cache(maxsize=16)
    def _get_userinfo(self, uid):
        resp = self.client.api_call('users.info', user=uid)
        self.log.debug(action='fetch.userinfo', user=uid)
        return resp['user']

    def _userify_message(self, message):
        uids = re.findall(r'\<@(\w+)\|?\w*\>', message)
        for uid in uids:
            username = '@' + self._get_userinfo(uid)['name']
            to_replace = '\<@%s\|?\w*\>' % uid
            message = re.sub(to_replace, username, message)
        return message

    def fetch_messages(self):
        """
        Read unprocessed messages in channel and store them in message queue
        """
        response = self.client.rtm_read()
        self.log.debug(action='events.fetched', count=len(response))
        new_messages = [
            m for m in response
            if m['type'] == 'message' and m['channel'] == self.id and 'user' in m
        ]
        for nm in new_messages:
            user = self._get_userinfo(nm['user'])
            nm['user'] = {
                'name': user['name'],
                'icon_url': user['profile']['image_512']
            }
            nm['text'] = self._userify_message(nm['text'])

        self.messages += new_messages
        if new_messages:
            self.log.info(action='messages.fetched', count=len(new_messages))

    def send_message(self, message):
        self.client.api_call(
            'chat.postMessage',
            channel='#' + self.name,
            text=message['text'],
            username=message['user']['name'],
            icon_url=message['user']['icon_url']
        )
        self.log.info(action='messages.sent', text=message['text'], user=message['user']['name'])


if __name__ == '__main__':
    with open(os.environ['CONFIG_PATH']) as f:
        config = yaml.safe_load(f)


    channels = [
        Channel(
            slack['domain'],
            slack['channel'],
            os.environ[slack['domain'].replace('-', '_').upper() + '_TOKEN']
        ) for slack in config['slacks']
    ]

    while True:
        for c in channels:
            c.fetch_messages()
        for c in channels:
            for m in c.messages:
                for target_c in channels:
                    if target_c is not c:
                        target_c.send_message(m)
            # Clear the messages! We've sent them all
            c.messages = []
            time.sleep(0.1)


