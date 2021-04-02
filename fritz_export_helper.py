import argparse
from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import ActionError, ServiceError, FritzInternalError
from pprint import pprint

parser = argparse.ArgumentParser(description='Check actions against Fritz TR-064 API and pretty print the results.')
parser.add_argument('fritz_ip', help='Fritz Device IP Address')
parser.add_argument('username', help='Username of a user on the Fritz device.')
parser.add_argument('password', help='Password of the user on the Fritz device.')
parser.add_argument('service', help='Username of a user on the Fritz device.')
parser.add_argument('action', help='Username of a user on the Fritz device.')

args = parser.parse_args()

#print(f'Would call IP: {args.fritz_ip}, user: {args.username}, password: {args.password}, service: {args.service}, action: {args.action}')
fc = FritzConnection(address=args.fritz_ip, user=args.username, password=args.password)

try:
    result = fc.call_action(args.service, args.action)
    print('--------------------------------\nRESULT:')
    pprint(result)
except (ServiceError, ActionError, FritzInternalError) as e:
    print(f'Calling service {args.service} with action {args.action} returned an error: {e}')