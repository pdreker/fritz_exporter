import argparse
import json
from pprint import pprint

from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import ActionError, FritzInternalError, ServiceError

parser = argparse.ArgumentParser(
    description="Check actions against Fritz TR-064 and AHA-HTTP API and pretty print the results."
)
parser.add_argument("fritz_ip", help="Fritz Device IP Address")
parser.add_argument("username", help="Username of a user on the Fritz device.")
parser.add_argument("password", help="Password of the user on the Fritz device.")
parser.add_argument(
    "-m",
    "--mode",
    choices=["tr064", "http"],
    default="tr064",
    help="Tell the helper which API to use (default: TR-064).",
)
parser.add_argument("-s", "--service", help="Service to call (only for TR-064).")
parser.add_argument("-a", "--action", help="Action to call.")
parser.add_argument("-i", "--ain", help="AIN of the device (only for HTTP).")
parser.add_argument(
    "-j",
    "--action_args",
    nargs="?",
    help="Optional arguments (as JSON dict string) to call (only for TR-064).",
)

args = parser.parse_args()

fc = FritzConnection(address=args.fritz_ip, user=args.username, password=args.password)

try:
    if args.mode == "http":
        if args.ain:
            if args.action is None:
                result = fc.call_http("getdeviceinfos", args.ain)
            else:
                result = fc.call_http(args.action, args.ain)
        else:
            args.action = "getdevicelistinfos"
            result = fc.call_http(args.action)
    else:  # noqa: PLR5501
        if args.action_args:
            arguments = json.loads(args.action_args)
            result = fc.call_action(args.service, args.action, arguments=arguments)
        else:
            result = fc.call_action(args.service, args.action)
    print("--------------------------------\nRESULT:")  # noqa: T201
    pprint(result)  # noqa: T203
except (ServiceError, ActionError, FritzInternalError) as e:
    print(f"Calling service {args.service} with action {args.action} returned an error: {e}")  # noqa: T201
