#    dropafile -- drop me a file on a webpage
#    Copyright (C) 2015  Uli Fouquet
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Drop a file on a webpage.
"""
import argparse
import os
import random
import ssl
import subprocess
import sys
import tempfile
from werkzeug import secure_filename
from werkzeug.serving import run_simple
from werkzeug.wrappers import Request, Response


PATH_MAP = {
    '/dropzone.js': ('dropzone.js', 'text/javascript'),
    '/dropzone.css': ('dropzone.css', 'text/css'),
    '/style.css': ('style.css', 'text/css'),
    '/index.html': ('page.html', 'text/html'),
    }


#: Chars allowed in passwords.
#: We allow plain ASCII chars and numbers, with some entitites removed,
#: that can be easily mixed up: letter `l` and number one, for instance.
ALLOWED_PWD_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789abcdefghjkmnpqrstuvwxyz'


def handle_options(args):
    """Handle commandline options.
    """
    parser = argparse.ArgumentParser(description="Start dropafile app.")
    parser.add_argument(
        '--host', required=False, default='localhost',
        help=(
            'Host we bind to. An IP address or DNS name. `localhost`'
            ' by default.'
            ),
        )
    parser.add_argument(
        '-p', '--port', required=False, default=8443, type=int,
        help=(
            'Port we listen at. An integer. 8443 by default.'
            )
        )
    opts = parser.parse_args(args)
    return opts


def get_random_password():
    """Get a password generated from `ALLOWED_PWD_CHARS`.

    The password entropy should be >= 128 bits. We use `SystemRandom()`,
    which should provide enough randomness to work properly.
    """
    rnd = random.SystemRandom()
    return ''.join(
        [rnd.choice(ALLOWED_PWD_CHARS) for x in range(23)])


class DropAFileApplication(object):

    def __init__(self, password=None, upload_dir=None):
        """Drop-A-File application.

        `password` is required to access the application's service. If
        none is provided, we generate one for you.
        """
        if password is None:
            password = get_random_password()
        self.password = password
        if upload_dir is None:
            upload_dir = tempfile.mkdtemp()
        self.upload_dir = upload_dir

    def check_auth(self, request):
        """Check basic auth against local password.

        We accept all usernames, but only _the_ password.
        """
        auth = request.authorization
        if auth is None:
            return False
        if auth.password != self.password:
            return False
        return True

    def authenticate(self):
        """Send 401 requesting basic auth from client.
        """
        return Response(
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">\n'
            '<title>401 Unauthorized</title>\n'
            '<h1>Unauthorized</h1>'
            '<p>You are not authorized to use this service.</p>',
            401, {'WWW-Authenticate': 'Basic realm="Login required"',
                  'Content-Type': 'text/html'}
            )

    def handle_uploaded_files(self, request):
        """Look for a upload file in `request`.

        If one is found, it is saved to `self.upload_dir`.
        """
        uploaded_file = request.files.get('file', None)
        if uploaded_file is None:
            return
        filename = secure_filename(uploaded_file.filename)
        path = os.path.join(self.upload_dir, filename)
        print("RECEIVED: %s" % path)
        uploaded_file.save(path)

    @Request.application
    def __call__(self, request):
        if not self.check_auth(request):
            return self.authenticate()
        self.handle_uploaded_files(request)
        path = request.path
        if path not in PATH_MAP.keys():
            path = '/index.html'
        filename, mimetype = PATH_MAP[path]
        with open(
                os.path.join(os.path.dirname(__file__), 'static', filename)
            ) as fd:
            page = fd.read()
        return Response(page, mimetype=mimetype)


def execute_cmd(cmd_list):
    """Excute the command `cmd_list`.

    Returns stdout and stderr output.
    """
    pipe = subprocess.PIPE
    proc = subprocess.Popen(
        cmd_list, stdout=pipe, stderr=pipe, shell=False)
    try:
        stdout, stderr = proc.communicate()
    finally:
        proc.stdout.close()
        proc.stderr.close()
        proc.wait()
    return stdout, stderr


def create_ssl_cert(path=None, bits=4096, days=2, cn='localhost',
                    country='US', state='', location=''):
    """Create an SSL cert and key in directory `path`.
    """
    print("Creating temporary self-signed SSL certificate...")
    if path is None:
        path = tempfile.mkdtemp()
    cert_path = os.path.join(path, 'cert.pem')
    key_path = os.path.join(path, 'cert.key')
    openssl_conf = os.path.join(os.path.dirname(__file__), 'openssl.conf')
    subject = '/C=%s/ST=%s/L=%s/O=%s/OU=%s/CN=%s/emailAddress=%s/' % (
        country, state, location, '', '', cn, '')
    cmd = [
        'openssl', 'req', '-x509', '-newkey', 'rsa:%s' % bits, '-nodes',
        '-out', cert_path, '-keyout', key_path, '-days', '%s' % days,
        '-sha256', '-config', openssl_conf, '-batch', "-subj", subject
        ]
    out, err = execute_cmd(cmd)
    print("Done.")
    print("Certificate in: %s" % cert_path)
    print("Key in:         %s" % key_path)
    return cert_path, key_path


def get_ssl_context(cert_path=None, key_path=None):
    """Get an SSL context to serve HTTP.

    If `cert_path` or `key_path` are ``None``, we create some. Then we
    add some modifiers (avail. with Python >= 2.7.9) to disable unsafe
    ciphers etc.

    The returned SSL context can be used with Werkzeug `run_simple`.
    """
    if (key_path is None) or (cert_path is None):
        cert_path, key_path = create_ssl_cert()
    ssl_context = (cert_path, key_path)
    if hasattr(ssl, 'SSLContext'):  # py >= 2.7.9
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        ssl_context.options |= ssl.OP_NO_SSLv2  # considered unsafe
        ssl_context.options |= ssl.OP_NO_SSLv3  # considered unsafe
        ssl_context.load_cert_chain(cert_path, key_path)
    return ssl_context


def run_server(args=None):
    if args is None:
        args = sys.argv
    options = handle_options(args[1:])
    ssl_context = get_ssl_context()
    sys.stdout.flush()
    application = DropAFileApplication()
    print("Password is: %s" % application.password)
    sys.stdout.flush()
    run_simple(options.host, options.port, application,
               ssl_context=ssl_context)
