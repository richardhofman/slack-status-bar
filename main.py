import json
import os
import signal
import sys

from CalendarStore import CalCalendarStore
from CalendarStore import NSDate
import CoreWLAN
import Quartz
import requests
import rumps
import yaml

APP_TITLE = 'Slack Status'
APP_ICON = os.path.join('icons', 'Slack_Icon.icns')
PREFERENCES = 'Preferences...'
AUTO = "Auto"

class SlackStatusBarApp(rumps.App):
    def __init__(self, config):
        super(SlackStatusBarApp, self).__init__(APP_TITLE, icon=APP_ICON)
        
        self.config = config
        self.statuses = config['statuses']
        self.status_menuitems = {}

        self.auto_menu_item = rumps.MenuItem(AUTO)
        self.auto_menu_item.state = True
        self.menu.add(self.auto_menu_item)

        # Begin statuses submenu
        self.statuses_submenu = rumps.MenuItem("Statuses")
        for status, info in self.statuses.items():
            menu_item = rumps.MenuItem(info['text'], icon=info['icon'])
            self.status_menuitems[status] = menu_item
            self.statuses_submenu.add(menu_item)
        self.menu.add(self.statuses_submenu)

        # End statuses submenu
        self.menu.add(None)

        self.pref_menu = rumps.MenuItem(PREFERENCES)
        self.menu.add(self.pref_menu)

    def no_op_callback(self, sender):
        pass

    @rumps.timer(60)
    def _check_status(self, sender):
        # Check if on vacation
        store = CalCalendarStore.defaultCalendarStore()

        for calendar in store.calendars():
            if calendar._.title in self.config['vacation_calendars']:
                pred = CalCalendarStore.\
                    eventPredicateWithStartDate_endDate_calendars_(
                        NSDate.date(), NSDate.date(), [calendar])
                event = store.eventsWithPredicate_(pred)
                if event:
                    self.set_status_by_name("vacation", event._.title[0])
                    return

        # Check if in a meeting
        for calendar in store.calendars():
            if calendar._.title in self.config['work_calendars']:
                pred = CalCalendarStore.\
                    eventPredicateWithStartDate_endDate_calendars_(
                        NSDate.date(), NSDate.date(), [calendar])
                event = store.eventsWithPredicate_(pred)
                if event:
                    self.set_status_by_name("meeting", event._.title[0])
                    return

        # Check if working remotely
        wifi_client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
        for interface in wifi_client.interfaces():
            if interface.ssid() is not None:
                if interface.ssid() == self.config['work_ssid']:
                    self.unset_status(None)
                else:
                    self.set_remote(None, interface.ssid())
                break

        # Check if screen is locked or asleep
        session = Quartz.CGSessionCopyCurrentDictionary()
        if session and session.get('CGSSessionScreenIsLocked', 0):
            self.set_presence_away(None)
        else:
            self.set_presence_auto(None)

    def _send_slack_status(self, status_text, status_emoji):
        url = 'https://slack.com/api/users.profile.set'
        profile = {'status_text': status_text, 'status_emoji': status_emoji}
        payload = {'token': self.config['token'],
                   'profile': json.dumps(profile)}
        try:
            r = requests.get(url, params=payload)
            if self.config['debug']:
                print(r.text)
        except requests.exceptions.ConnectionError as err:
            if self.config['debug']:
                print('ConnectionError: %s' % err.message)

    @rumps.clicked(AUTO)
    def set_auto(self, sender):
        sender.state = not sender.state
        if sender.state:
            # Turning auto mode ON
            self.set_presence_auto(sender)

            # Disable all callbacks (grays out menu items)
            for menuitem in self.status_menuitems.values():
                menuitem.set_callback(None)

            # Enable timer
            for timer in rumps.timers():
                timer.start()
        elif not sender.state:
            # Turning auto mode OFF

            # Disable timer
            for timer in rumps.timers():
                timer.stop()

            # Enable all callbacks
            for menuitem in self.status_menuitems.values():
                menuitem.set_callback(self.set_status)

    def reverse_lookup_menuitem(self, target):
        for status, item in self.status_menuitems.items():
            if target.title == item.title:
                return status

    def unset_status(self, sender):
        self._send_slack_status('', '')

    def set_status(self, sender):
        status = self.reverse_lookup_menuitem(sender)
        self.set_status_by_name(status)
    
    def set_status_by_name(self, status, extra_message=None):
        if self.config['meeting_title'] is True and extra_message != None:
            status_message = self.statuses[status]['text'] + ': ' + extra_message
        else:
            status_message = self.statuses[status]["text"]
        status_icon = self.statuses[status]["slack_icon_name"]
        self._send_slack_status(status_message, status_icon)

    def set_remote(self, sender, ssid=None):
        if ssid and 'remote_locations' in self.config:
            for location in self.config['remote_locations']:
                if location['ssid'] == ssid:
                    self._send_slack_status(location['status_text'],
                                            location['status_emoji'])
                    return
        else:
            self.set_status_by_name("wfr")

    def set_presence_auto(self, sender):
        url = 'https://slack.com/api/users.setPresence'
        payload = {'token': self.config['token'], 'presence': 'auto'}
        try:
            r = requests.get(url, params=payload)
            if self.config['debug']:
                print(r.text)
        except requests.exceptions.ConnectionError as err:
            if self.config['debug']:
                print('ConnectionError: %s' % err.message)

    def set_presence_away(self, sender):
        url = 'https://slack.com/api/users.setPresence'
        payload = {'token': self.config['token'], 'presence': 'away'}
        try:
            r = requests.get(url, params=payload)
            if self.config['debug']:
                print(r.text)
        except requests.exceptions.ConnectionError as err:
            if self.config['debug']:
                print('ConnectionError: %s' % err.message)

    @rumps.clicked(PREFERENCES)
    def preferences(self, sender):
        default_text = ''
        if 'token' in self.config and self.config['token']:
            default_text = self.config['token']
        pref_window = rumps.Window(message='Enter token:',
                                   default_text=default_text, cancel=True)
        pref_window.icon = APP_ICON
        response = pref_window.run()
        if response.clicked and response.text:
            self.config['token'] = response.text

def _signal_handler(signal, frame):
    rumps.quit_application()


def main():
    # Read the configuration file
    config_file = os.path.join(rumps.application_support(APP_TITLE),
                               'config.yaml')
    with open(config_file, 'r') as conf:
        try:
            config = yaml.safe_load(conf)
        except yaml.YAMLError as exc:
            print(exc)
            return

    # Setup our CTRL+C signal handler
    signal.signal(signal.SIGINT, _signal_handler)

    # Enable debug mode
    rumps.debug_mode(config['debug'])

    # Startup application
    SlackStatusBarApp(config).run()

if __name__ == "__main__":
    sys.exit(main())
