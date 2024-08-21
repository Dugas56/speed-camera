# Speed-Camera Project with Web Interface

This project is an extension of the original [Speed-Camera](https://github.com/pageauc/speed-camera) project by Claude Pageau. The main goal of this project is to add a web interface that allows for remote control and monitoring of the Speed-Camera app on a Raspberry Pi 3 (RPI3).

## Overview

The Speed-Camera project is a powerful tool designed to monitor and capture images of objects moving above a specified speed threshold. This project extends the functionality by adding a web interface, enabling remote control of the Speed-Camera app via a browser.

### Features

- **Web Interface**: Control the Speed-Camera application remotely.
- **Remote Monitoring**: View captured images and data from anywhere.
- **Raspberry Pi 3 Compatibility**: Optimized for use on the Raspberry Pi 3.

## Setup

1. **Clone the Repository**

   ```bash
   git clone https://github.com/yourusername/speed-camera-web.git
   cd speed-camera-web

### Make sure your Raspberry Pi 3 is set up with the necessary libraries and dependencies:

sudo apt-get update
sudo apt-get install -y python3 python3-pip
pip3 install -r requirements.txt

### Start the Speed-Camera application with the web interface:
python3 speed_camera_web.py

### Open a web browser and navigate to:
http://<your-raspberry-pi-ip>:5000

Here, you can control and monitor the Speed-Camera app remotely.

### Acknowledgments
This project is based on the original Speed-Camera by Claude Pageau. All credit for the underlying speed detection algorithm goes to him.

### Contributing
Feel free to submit issues and pull requests. Contributions are welcome!

### License
This project is licensed under the MIT License - see the LICENSE file for details.





