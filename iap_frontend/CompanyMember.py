from typing import Dict
import ldap3
from ldap3.core.exceptions import LDAPBindError
import re
class CompanyMember:
    LDAPServer = {
        'TPE': {
            'host': 'ldap://10.110.15.1', 'port': 389, 'domain': 'compal', 'DC': 'DC=compal,DC=com'
        },
        'KS': {
            'host': 'ldap://10.129.128.100', 'port': 389, 'domain': 'gi', 'DC': 'DC=gi,DC=compal,DC=com'
        },
        'VN': {
            'host': 'ldap://10.144.2.100', 'port': 389, 'domain': 'vn', 'DC': 'DC=vn,DC=compal,DC=com'
        },
        'CD': {
            'host': 'ldap://10.129.128.100', 'port': 389, 'domain': 'gi', 'DC': 'DC=gi,DC=compal,DC=com'
        }
    }

    def __init__(self, name: str, password: str, plant: str):
        try:
            serverInfo = CompanyMember.LDAPServer[plant]
        except KeyError:
            raise (f'Plant {plant} does not exist.')

        server = ldap3.Server(host=serverInfo['host'], port=serverInfo['port'])
        user = f'{serverInfo["domain"]}\\{name}'

        try:
            self.__connection = ldap3.Connection(server=server, user=user, password=password, auto_bind=True)
        except LDAPBindError:
            raise (f'Member {name} does not exist.')

    def getIdAndDepartment(self, name: str, plant: str) -> Dict[str, str]:
        try:
            DC = CompanyMember.LDAPServer[plant]['DC']
        except KeyError:
            raise (f'Plant {plant} does not exist.')
        self.__connection.search(
            search_base=DC,
            search_filter=f'(sAMAccountName={name})',
            search_scope=ldap3.SUBTREE,
            attributes=['employeeID', 'department'])
        try:
            employeeId = str(self.__connection.entries[0].employeeID)
            department = str(self.__connection.entries[0].department).split("\\")[-1]
            username = re.search(r"CN=([^,]+)", str(self.__connection.entries[0])).group(1) if self.__connection.entries else None
            return {'employeeId': employeeId, 'department': department, 'username': username}
        except IndexError:
            raise (f'Member {name} does not exist.')