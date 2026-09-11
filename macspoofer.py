import os
import random
import argparse

def generate_random_mac():
    # Locally administered MAC (02:xx:xx:xx:xx:xx)
    mac = [0x02, random.randint(0x00, 0xff),
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff)]
    return ':'.join(map(lambda x: "%02x" % x, mac))

def change_mac(interface, new_mac):
    print(f"[+] Changing MAC for {interface} to {new_mac}")
    os.system(f"netsh interface set interface name=\"{interface}\" admin=disable")
    os.system(f"reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\Class\\{interface}\\ /v NetworkAddress /d {new_mac.replace(':','')} /f")
    os.system(f"netsh interface set interface name=\"{interface}\" admin=enable")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--interface", required=True, help="Network adapter name")
    parser.add_argument("-m", "--mac", help="Custom MAC address (format: XX:XX:XX:XX:XX:XX)")
    args = parser.parse_args()

    new_mac = args.mac if args.mac else generate_random_mac()
    change_mac(args.interface, new_mac)

