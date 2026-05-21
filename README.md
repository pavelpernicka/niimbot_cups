# Nimbot B1 CUPS Driver

`CUPS` driver for `Niimbot B1` label printer on Linux, supports both - bluetooth and USB printing.
Based on [niimprint](https://github.com/AndBondStyle/niimprint) and [niim.blue docs](https://printers.niim.blue/)

## Preparation

On Debian/Ubuntu install:
```bash
sudo apt install cups python3 python3-pil python3-serial poppler-utils bluez
```


```bash
bluetoothctl
scan on
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
info XX:XX:XX:XX:XX:XX
```
XX:XX:XX:XX:XX:XX is printer bluetooth MAC address, you can get it from sticker that printer prints if you double click power button, or you can find it in bluetooth discovered devices. 
In discovered devices there will be two of them - choose that one with rfcomm.


## Installation

### Driver installation

Run:

```bash
sudo ./scripts/install_cups_driver.sh
```

### Print queue

Now we can create print queue se the printer appears on list in GUI. Depending on your connection method, run:

#### Bluetooth

```bash
sudo lpadmin -p NimbotB1-BT -E \
  -v 'nimbot://bluetooth?address=XX%3AXX%3AXX%3AXX%3AXX%3AXX&model=b1' \
  -P /usr/share/ppd/nimbot/Nimbot-B1.ppd
```
#### USB

```bash
sudo lpadmin -p NimbotB1-USB -E \
  -v 'nimbot://usb?port=%2Fdev%2FttyACM0&model=b1' \
  -P /usr/share/ppd/nimbot/Nimbot-B1.ppd
```
Instead of `/dev/ttyACM0` choose device that appears.

### First test

Bluetooth:

```bash
printf 'hello\n' | lp -d NimbotB1-BT -o PageSize=w30h15
```

USB:

```bash
printf 'hello\n' | lp -d NimbotB1-USB -o PageSize=w30h15
```

## Debuging

For debuging, you can print directly without using CUPS:

```bash
sudo python3 ./scripts/print_test_label.py \
  --device-uri 'nimbot://bluetooth?address=XX:XX:XX:XX:XX:XX&model=b1' \
  --text 'hello' \
  --width-mm 30 \
  --height-mm 15 \
  --density 3 \
  --timeout 20 \
  --verbose
```
