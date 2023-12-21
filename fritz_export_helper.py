import argparse
import json
from pprint import pprint

from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import ActionError, FritzInternalError, ServiceError

parser = argparse.ArgumentParser(
    description="Check actions against Fritz TR-064 API and pretty print the results."
)
parser.add_argument("fritz_ip", help="Fritz Device IP Address")
parser.add_argument("username", help="Username of a user on the Fritz device.")
parser.add_argument("password", help="Password of the user on the Fritz device.")
parser.add_argument("service", help="Service to call.")
parser.add_argument("action", help="Action to call.")
parser.add_argument("action_args", nargs="?", help="Optional arguments to call (JSON dict string).")

args = parser.parse_args()

fc = FritzConnection(address=args.fritz_ip, user=args.username, password=args.password)

try:
    if args.action_args:
        arguments = json.loads(args.action_args)
        result = fc.call_action(args.service, args.action, arguments=arguments)
    else:
        result = fc.call_action(args.service, args.action)
    print("--------------------------------\nRESULT:")
    pprint(result)
except (ServiceError, ActionError, FritzInternalError) as e:
    print(f"Calling service {args.service} with action {args.action} returned an error: {e}")
