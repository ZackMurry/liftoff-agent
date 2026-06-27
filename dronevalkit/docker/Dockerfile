FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Base dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    curl \
    git \
    gnupg \
    lsb-release \
    python3 \
    python3-pip \
    sudo \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Gazebo Harmonic
RUN curl https://packages.osrfoundation.org/gazebo.gpg \
    --output /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] https://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
    | tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null \
    && apt-get update \
    && apt-get install -y gz-harmonic \
    && rm -rf /var/lib/apt/lists/*

# Clone PX4
WORKDIR /root
RUN git clone --recursive https://github.com/PX4/PX4-Autopilot.git

# Run PX4's Ubuntu setup script (installs remaining deps)
RUN cd PX4-Autopilot \
    && bash Tools/setup/ubuntu.sh --no-nuttx \
    && rm -rf /var/lib/apt/lists/*

# Install Python tools for dronevalkit
RUN pip3 install mavsdk pyulog numpy pandas matplotlib

# Build PX4 SITL (so first run is fast)
RUN cd PX4-Autopilot && make px4_sitl_default

RUN mkdir -p /root/dronevalkit
COPY start_multi.sh /root/dronevalkit/start_multi.sh
RUN chmod +x /root/dronevalkit/start_multi.sh

# Expose MAVLink ports
# 14540: MAVSDK/offboard API
# 14550: QGroundControl
# 18570: Gazebo (optional, for GUI forwarding)
EXPOSE 14540/udp 14550/udp 18570

WORKDIR /root/PX4-Autopilot

CMD ["/bin/bash"]
