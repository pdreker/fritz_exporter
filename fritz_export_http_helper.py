import argparse
import json
from pprint import pprint

from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import ActionError, FritzInternalError, ServiceError

parser = argparse.ArgumentParser(
    description="Perform the selected  against Fritz AHA-HTTP API and pretty print the results."
)
parser.add_argument("fritz_ip", help="Fritz Device IP Address")
parser.add_argument("username", help="Username of a user on the Fritz device.")
parser.add_argument("password", help="Password of the user on the Fritz device.")
parser.add_argument("--action", help="Action to be performed on the device.")
parser.add_argument("--ain", help="AIN of the device.")
# parser.add_argument("action_args", nargs="?", help="Optional arguments to call (JSON dict string).")

args = parser.parse_args()

fc = FritzConnection(address=args.fritz_ip, user=args.username, password=args.password)

try:
    if args.ain:
        if args.action == None:
            result = fc.call_http('getdeviceinfos', args.ain)
        else:
            result = fc.call_http(args.action, args.ain)
    else:
        args.action= 'getdevicelistinfos'
        result = fc.call_http(args.action)
    print("--------------------------------\nRESULT:")  # noqa: T201
    pprint(result)  # noqa: T203
except (ServiceError, ActionError, FritzInternalError) as e:
    print(f"Calling service {args.service} with action {args.action} returned an error: {e}")  # noqa: T201

