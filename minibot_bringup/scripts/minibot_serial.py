#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped

from tf2_ros import TransformBroadcaster
from tf_transformations import quaternion_from_euler, euler_from_quaternion

import serial
import time
import numpy as np
import math

class motors_command(object):
    command = [0xfa, 0xfe, 0x01, 0, 0x1, 0x3, 0, 0xfa, 0xfd]
    
class send_cmd_to_controller(object):
    command = [0xfa, 0xfe, 0x2, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x9, 0x0, 0xfa, 0xfd]

class get_state(object):
    command = [0xfa, 0xfe, 0x3, 0x1, 0x4, 0xfa, 0xfd]

class OdomPose(object):
    x = 0.0
    y = 0.0
    theta = 0.0
    timestamp = 0
    pre_timestamp = 0

class OdomVel(object):
    x = 0.0
    y = 0.0
    w = 0.0

class Joint(object):
    joint_name = ['wheel_left_joint', 'wheel_right_joint']
    joint_pos = [0.0, 0.0]
    joint_vel = [0.0, 0.0]

class Minibotserial(Node):
    def __init__(self):
        super().__init__('serial')
        arduino_port = '/dev/ttyUSB0'
        baud_rate = 1000000
        self.ser = serial.Serial(arduino_port, baud_rate)
        time.sleep(3)
        self.get_logger().info('아두이노 연결!!')

        self.odom_pub = self.create_publisher(Odometry, '/odom', 10)
        self.cmd_sub = self.create_subscription(Twist, '/cmd_vel', self.cmd_callback, 10)
        # self.pub_joint_states = self.create_publisher( JointState, 'joint_states', 10)
        
        self.l_lamp = 0
        self.r_lamp = 0
        
        self.odom_pose = OdomPose()
        self.odom_vel = OdomVel()
        self.joint = Joint()
        self.odom_broadcaster = TransformBroadcaster(self)

        self.odom_pose.pre_timestamp = self.get_clock().now().to_msg()
        self.l_last_enc, self.r_last_enc = 0.0, 0.0

        self.hw_commands = [0, 0]
        self.hw_positions = [0, 0]
        
        self.recv_buf = None #np.zeros(20, dtype=np.uint8)
        self.last_distance = 0

        self.cnt = 0

        self.create_timer(0.01, self.update_robot)
        self.get()
        self.enable_motors()
        time.sleep(1)

    def update_robot(self):
        self.get()
        self.update_odometry(self.l_pos_enc, self.r_pos_enc, self.l_last_enc, self.r_last_enc)
        #self.updateJointStates(self.l_pos_enc, self.r_pos_enc, self.l_last_enc, self.r_last_enc)
        # if self.cnt == 50:
        #     self.stop_wheel()

        # self.cnt += 1

    def cmd_callback(self, msg):
         self.get()
         self.hw_commands = [msg.linear.x , msg.linear.x ] #기본 cmd_vel은 liner.x가 0.5이어서 너무빨라서 5를 나누었다
         if msg.angular.z != 0:
            self.hw_commands = [-msg.angular.z / 10, msg.angular.z / 10]  # 10으로 나눔
         self.wheel()
         self.cnt = 0

    def read(self, size=1, timeout=None):
        self.ser.timeout = timeout
        readed = self.ser.read(size)
        return readed
    
    def wheel(self):
        l_cmd = (self.hw_commands[0] * 44.0 / (2.0 * np.pi) * 56.0) * -1.0
        r_cmd = (self.hw_commands[1] * 44.0 / (2.0 * np.pi) * 56.0)
        self.send_comand(int(l_cmd), int(r_cmd), self.l_lamp, self.r_lamp)

    def stop_wheel(self):
        l_cmd = 0
        r_cmd = 0
        self.send_comand(int(l_cmd), int(r_cmd), self.l_lamp, self.r_lamp)

    def enable_motors(self):
        command = motors_command.command
        command[3] = 1
        command[6] = np.uint8(sum(command[2:6]))
        self.ser.write(bytes(command))
        readed = self.read(size=20, timeout=1)
        self.get_logger().info('모터 on!!')

    def send_comand(self, l_vel, r_vel, l_lamp, r_lamp):
        command = send_cmd_to_controller.command
        command[3] = self.enabled
        command[4] = (l_vel >> 8) & 0xFF
        command[5] = l_vel & 0xFF
        command[6] = (r_vel >> 8) & 0xFF
        command[7] = r_vel & 0xFF
        command[8] = l_lamp
        command[9] = r_lamp
        command[12] =  np.uint8(sum(command[2:12]))
        self.ser.write(bytes(command))
        self.get_logger().info('send!!')

    def get(self):
        command = get_state.command
        self.ser.write(bytes(command))

        try:
            self.recv_buf = self.read(size=20, timeout=1)
            if self.recv_buf[2] != 0x93:
                self.get_logger().error( "Failed to enable motors... check the boards...")
                return
        except:
            self.get_logger().error("Exceptions: \033[91m%s\033[0m")
            return
        
        self.enabled = self.recv_buf[3]
        self.l_pos_enc = (self.recv_buf[4] << 24) + (self.recv_buf[5] << 16) + (self.recv_buf[6] << 8) + self.recv_buf[7]
        self.r_pos_enc = (self.recv_buf[8] << 24) + (self.recv_buf[9] << 16) + (self.recv_buf[10] << 8) + self.recv_buf[11]
        self.l_lamp_val = self.recv_buf[12]
        self.r_lamp_val = self.recv_buf[13]
        self.range_sensor_val = (self.recv_buf[14] << 8) + self.recv_buf[15]

        self.l_lamp = self.l_lamp_val
        self.r_lamp = self.r_lamp_val
        
    def update_odometry(self, l_pos_enc, r_pos_enc, l_last_enc, r_last_enc):
        if l_pos_enc - l_last_enc > 1000 or l_pos_enc - l_last_enc < -1000:
            pass
        else:
            self.hw_positions[0] = (l_pos_enc - l_last_enc) / 44.0 / 28.0 * (2.0 * np.pi) * -1.0 

        if r_pos_enc - r_last_enc > 1000 or r_pos_enc - r_last_enc < - 1000:
            pass
        else: 
            self.hw_positions[1] = (r_pos_enc - r_last_enc) / 44.0 / 28.0 * (2.0 * np.pi)

        self.l_last_enc = l_pos_enc
        self.r_last_enc = r_pos_enc

        print(self.hw_positions)

        delta_distance = (self.hw_positions[0] + self.hw_positions[1]) / 2.0 *0.1
        delta_theta = (self.hw_positions[1] - self.hw_positions[0]) / 28.0 * 2 * np.pi
        
        trans_vel = delta_distance
        orient_vel = delta_theta

        self.odom_pose.timestamp = self.get_clock().now().to_msg()
        dt = (self.odom_pose.timestamp.sec - self.odom_pose.pre_timestamp.sec) + (self.odom_pose.timestamp.nanosec - self.odom_pose.pre_timestamp.nanosec)

        self.odom_pose.pre_timestamp = self.odom_pose.timestamp
        self.odom_pose.theta += orient_vel #* dt

        d_x = trans_vel * math.cos(self.odom_pose.theta) 
        d_y = trans_vel * math.sin(self.odom_pose.theta)

        self.odom_pose.x += d_x 
        self.odom_pose.y += d_y 

        odom_orientation_quat = quaternion_from_euler(0, 0, self.odom_pose.theta)
        
        self.odom_vel.x = trans_vel
        self.odom_vel.y = 0.
        self.odom_vel.w = orient_vel

        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.transform.translation.x = self.odom_pose.x
        t.transform.translation.y = self.odom_pose.y
        t.transform.translation.z = 0.0
        t.header.frame_id = "odom"
        t.child_frame_id = "base_footprint"
        t.transform.rotation.x = odom_orientation_quat[0]
        t.transform.rotation.y = odom_orientation_quat[1]
        t.transform.rotation.z = odom_orientation_quat[2]
        t.transform.rotation.w = odom_orientation_quat[3]
        self.odom_broadcaster.sendTransform(t)

        odom = Odometry()
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_footprint"
        odom.header.stamp = self.get_clock().now().to_msg()
        odom.pose.pose.position.x = self.odom_pose.x
        odom.pose.pose.position.y = self.odom_pose.y
        odom.pose.pose.position.z = 0.
        odom.pose.pose.orientation.x = odom_orientation_quat[0]
        odom.pose.pose.orientation.y = odom_orientation_quat[1]
        odom.pose.pose.orientation.z = odom_orientation_quat[2]
        odom.pose.pose.orientation.w = odom_orientation_quat[3]
        odom.twist.twist.linear.x = self.odom_vel.x
        odom.twist.twist.linear.y = self.odom_vel.y
        odom.twist.twist.linear.z = 0.
        odom.twist.twist.linear.z = 0.
        odom.twist.twist.linear.z = self.odom_vel.w
        self.odom_pub.publish(odom)

    # def updateJointStates(self, l_pos_enc, r_pos_enc, l_last_enc, r_last_enc):
    #     wheel_ang_left = odo_l / self.wheel_radius
    #     wheel_ang_right = odo_r / self.wheel_radius

    #     wheel_ang_vel_left = (trans_vel - (self.wheel_base / 2.0) * orient_vel) / self.wheel_radius
    #     wheel_ang_vel_right = (trans_vel + (self.wheel_base / 2.0) * orient_vel) / self.wheel_radius

    #     self.joint.joint_pos = [wheel_ang_left, wheel_ang_right]
    #     self.joint.joint_vel = [wheel_ang_vel_left, wheel_ang_vel_right]

    #     joint_states = JointState()
    #     joint_states.header.frame_id = "base_link"
    #     joint_states.header.stamp = self.get_clock().now().to_msg()
    #     joint_states.name = self.joint.joint_name
    #     joint_states.position = self.joint.joint_pos
    #     joint_states.velocity = self.joint.joint_vel
    #     joint_states.effort = []

    #     self.pub_joint_states.publish(joint_states)
                

def main(args=None):
    rclpy.init(args=args)

    node = Minibotserial()

    rclpy.spin(node)

    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()