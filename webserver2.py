import html
import os
import subprocess
import socket
import fcntl
import struct
import socketserver
import sys
import time
import urllib
from http.server import SimpleHTTPRequestHandler
from io import BytesIO

PROG_VER = "ver 13.3 written by Claude Pageau modified by Alexandre Strube for python3 compatibility"

# Find the full path of this python script
SCRIPT_PATH = os.path.abspath(__file__)
# Get the path location only (excluding script name)
BASE_DIR = os.path.dirname(SCRIPT_PATH)
PROG_NAME = os.path.basename(__file__)    # Name of this program
CONFIG_FILE_PATH = os.path.join(BASE_DIR, "config.py")

if not os.path.exists(CONFIG_FILE_PATH):
    print("ERROR - Cannot Import Configuration Variables.")
    print("        Missing Configuration File %s" % CONFIG_FILE_PATH)
    sys.exit(1)
else:
    print("Importing Configuration Variables from File %s" % CONFIG_FILE_PATH)
    from config import *

os.chdir(WEB_SERVER_ROOT)
web_root = os.getcwd()
os.chdir(BASE_DIR)
MNT_POINT = "./"

if WEB_LIST_BY_DATETIME_ON:
    dir_sort = 'Sort DateTime'
else:
    dir_sort = 'Sort Filename'

if WEB_LIST_BY_DATETIME_ON:
    dir_order = 'Desc'
else:
    dir_order = 'Asc'

list_title = "%s %s" % (dir_sort, dir_order)


def get_ip_address(ifname):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack('256s', ifname[:15])
        )[20:24])
    except IOError:
        return None


def df(drive_mnt):
    try:
        df = subprocess.Popen(["df", "-h", drive_mnt], stdout=subprocess.PIPE)
        output = df.communicate()[0].decode('utf-8')
        device, size, used, available, percent, mountpoint = output.split("\n")[
            1].split()
        drive_status = ("Drive %s at %s [Space %s Used %s of %s Avail %s]" %
                        (device, mountpoint, percent, used, size, available))
    except:
        drive_status = "df command Error. No drive status avail"
    return drive_status


class DirectoryHandler(SimpleHTTPRequestHandler):
    def list_directory(self, path):
        try:
            list = os.listdir(path)
            all_entries = len(list)
        except os.error:
            self.send_error(404, b"No permission to list directory")
            return None

        if WEB_LIST_BY_DATETIME_ON:
            list.sort(key=lambda x: os.stat(os.path.join(path, x)
                                            ).st_mtime, reverse=WEB_LIST_BY_DATETIME_ON)
        else:
            list.sort(key=lambda a: a.lower(), reverse=WEB_LIST_BY_DATETIME_ON)
        f = BytesIO()
        displaypath = html.escape(urllib.parse.unquote(self.path))

        file_found = False
        cnt = 0
        for entry in list:
            fullname = os.path.join(path, entry)
            if os.path.islink(fullname) or os.path.isfile(fullname):
                file_found = True
                break
            cnt += 1

        f.write(b'<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">')
        f.write(b'<head>')
        f.write(b'<meta "Content-Type" content="txt/html; charset=ISO-8859-1" />')
        f.write(
            b'<meta name="viewport" content="width=device-width, initial-scale=1.0" />')
        if WEB_PAGE_REFRESH_ON:
            f.write(b'<meta http-equiv="refresh" content="%s" />' %
                    WEB_PAGE_REFRESH_SEC.encode('utf-8'))
        f.write(b'</head>')

        tpath, cur_folder = os.path.split(self.path)
        f.write(b"<html><title>%s %s</title>" %
                (WEB_PAGE_TITLE.encode('utf-8'), self.path.encode('utf-8')))
        f.write(b"<body>")
        f.write(b"""
            <script>
            document.onkeydown = checkKey;

            function checkKey(e) {
                e = e || window.event;
                var indexOfCurrentImg = Math.max(0, Array.from(document.querySelectorAll('a[target="imgbox"')).map((a) => a.href).indexOf(document.getElementsByTagName("iframe")[0].contentDocument.URL ));
                var nextA = Array.from(document.querySelectorAll('a[target="imgbox"'))[indexOfCurrentImg-1];
                var prevA = Array.from(document.querySelectorAll('a[target="imgbox"'))[indexOfCurrentImg+1];

                if (e.keyCode == '37') { // left arrow
                    prevA.click();
                }
                else if (e.keyCode == '39') {  // right arrow
                    nextA.click();
                }
            }

            function startApp() {
                console.log("Start button clicked");
                fetch('/start-app').then(response => {
                    if (response.ok) {
                        console.log("Start app command sent successfully");
                        setTimeout(() => {
                            location.reload();
                        }, 1000);
                    } else {
                        console.error("Failed to send start app command");
                    }
                }).catch(error => {
                    console.error("Error sending start app command:", error);
                });
            }

            function stopApp() {
                console.log("Stop button clicked");
                fetch('/stop-app').then(response => {
                    if (response.ok) {
                        console.log("Stop app command sent successfully");
                        setTimeout(() => {
                            location.reload();
                        }, 1000);
                    } else {
                        console.error("Failed to send stop app command");
                    }
                }).catch(error => {
                    console.error("Error sending stop app command:", error);
                });
            }
            </script>
        """)

        f.write(b'<center><b>%s &nbsp &nbsp &nbsp</b>' %
                WEB_PAGE_TITLE.encode('utf-8'))
        f.write(b'<em>Note: Left/Right Arrow Keys Scroll File List</em></div></center>')

        f.write(b'<iframe width="%s" height="%s" align="left"'
                % (WEB_IFRAME_WIDTH_PERCENT.encode('utf-8'), WEB_IMAGE_HEIGHT.encode('utf-8')))
        if file_found:
            f.write(b'src="%s" name="imgbox" id="imgbox" alt="%s">'
                    % (list[cnt].encode('utf-8'), WEB_PAGE_TITLE.encode('utf-8')))
        else:
            f.write(b'src="%s" name="imgbox" id="imgbox" alt="%s">'
                    % (b"about:blank", WEB_PAGE_TITLE.encode('utf-8')))

        f.write(b'<p>iframes are not supported by your browser.</p></iframe>')

        list_style = b'<div style="height: ' + \
            WEB_LIST_HEIGHT.encode(
                'utf-8') + b'px; overflow: auto; white-space: nowrap;">'
        f.write(list_style)

        refresh_button = ('''<FORM>&nbsp;&nbsp;<INPUT TYPE="button" onClick="history.go(0)"
            VALUE="Refresh">&nbsp;&nbsp;<b>%s</b></FORM>''' % list_title)
        f.write(b'%s' % refresh_button.encode('utf-8'))
        f.write(
            b'<ul name="menu" id="menu" style="list-style-type:none; padding-left: 4px">')

        if self.path != "/":
            f.write(b'<li><a href="%s" >%s</a></li>\n'
                    % (urllib.parse.quote("..").encode('utf-8'), html.escape("< BACK").encode('utf-8')))
        display_entries = 0
        file_found = False
        for name in list:
            display_entries += 1
            if WEB_MAX_LIST_ENTRIES > 1:
                if display_entries >= WEB_MAX_LIST_ENTRIES:
                    break
            fullname = os.path.join(path, name)
            displayname = linkname = name
            date_modified = time.strftime(
                '%H:%M:%S %d-%b-%Y', time.localtime(os.path.getmtime(fullname)))
            if os.path.islink(fullname):
                displayname = name + "@"
            if os.path.isdir(fullname):
                displayname = name + "/"
                linkname = os.path.join(displaypath, displayname)
                f.write(b'<li><a href="%s" >%s</a></li>\n'
                        % (urllib.parse.quote(linkname).encode('utf-8'), html.escape(displayname).encode('utf-8')))
            else:
                f.write(b'<li><a href="%s" target="imgbox">%s</a> - %s</li>\n'
                        % (urllib.parse.quote(linkname).encode('utf-8'), html.escape(displayname).encode('utf-8'), date_modified.encode('utf-8')))
        if (self.path != "/") and display_entries > 35:
            f.write(b'<li><a href="%s" >%s</a></li>\n' %
                    (urllib.parse.quote("..").encode('utf-8'), html.escape("< BACK").encode('utf-8')))
        f.write(b'</ul>\n')
        f.write(b'</div>\n')
        f.write(b'<br style="clear: left;">')

        f.write(b'''
        <button onclick="startApp()">Start App</button>
        <button onclick="stopApp()">Stop App</button>
        ''')

        f.write(b'</body></html>\n')
        length = f.tell()
        f.seek(0)
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        return f

    def do_GET(self):
        if self.path == '/start-app':
            # Start the app
            subprocess.Popen(['python3', 'speedcam.py'])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b'<html><script>window.location.reload();</script><body>App started</body></html>')
        elif self.path == '/stop-app':
            # Stop the app
            subprocess.Popen(['./speed-cam.sh', 'stop'])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b'<html><script>window.location.reload();</script><body>App stopped</body></html>')
        else:
            super().do_GET()


if __name__ == "__main__":
    with socketserver.TCPServer(("", WEB_SERVER_PORT), DirectoryHandler) as httpd:
        print("Serving HTTP on port %d..." % WEB_SERVER_PORT)
        httpd.serve_forever()

