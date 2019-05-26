from absl import app
from absl import flags

import erdos.graph
from erdos.operators import RecordOp
from erdos.operators import ReplayOp

import config
import operator_creator

# Import operators that interact with the simulator.
from simulation.carla_legacy_operator import CarlaLegacyOperator

FLAGS = flags.FLAGS
RGB_CAMERA_NAME = 'front_rgb_camera'
DEPTH_CAMERA_NAME = 'front_depth_camera'
SEGMENTED_CAMERA_NAME = 'front_semantic_camera'


def create_carla_op(graph, camera_setups):
    carla_op = graph.add(
        CarlaLegacyOperator,
        name='carla',
        init_args={
            'flags': FLAGS,
            'camera_setups': camera_setups,
            'lidar_stream_names': [],
            'log_file_name': FLAGS.log_file_name,
            'csv_file_name': FLAGS.csv_log_file_name
        },
        setup_args={
            'camera_setups': camera_setups,
            'lidar_stream_names': []
        })
    return carla_op


def main(argv):

    # Define graph
    graph = erdos.graph.get_current_graph()

    rgb_camera_setup = (RGB_CAMERA_NAME,
                        'SceneFinal',
                        (FLAGS.carla_camera_image_width,
                         FLAGS.carla_camera_image_height),
                        (2.0, 0.0, 1.4))
    camera_setups = [rgb_camera_setup,
                     (DEPTH_CAMERA_NAME, 'Depth',
                      (FLAGS.carla_camera_image_width,
                       FLAGS.carla_camera_image_height),
                      (2.0, 0.0, 1.4)),
                     (SEGMENTED_CAMERA_NAME, 'SemanticSegmentation',
                      (FLAGS.carla_camera_image_width,
                       FLAGS.carla_camera_image_height),
                      (2.0, 0.0, 1.4))]

    # Add operators to the graph.
    carla_op = create_carla_op(graph, camera_setups)

    # Add visual operators.
    operator_creator.add_visualization_operators(
        graph, carla_op, RGB_CAMERA_NAME, DEPTH_CAMERA_NAME)

    # Add recording operators.
    operator_creator.add_recording_operators(
        graph, carla_op, RGB_CAMERA_NAME, DEPTH_CAMERA_NAME)

    segmentation_ops = []
    if FLAGS.segmentation_drn:
        segmentation_op = operator_creator.create_segmentation_drn_op(graph)
        segmentation_ops.append(segmentation_op)

    if FLAGS.segmentation_dla:
        segmentation_op = operator_creator.create_segmentation_dla_op(graph)
        segmentation_ops.append(segmentation_op)

    graph.connect([carla_op], segmentation_ops)

    if FLAGS.evaluate_segmentation:
        eval_segmentation_op = operator_creator.create_segmentation_eval_op(
            graph, carla_op, segmentation_op,
            SEGMENTED_CAMERA_NAME, 'segmented_stream')
        graph.connect([carla_op] + segmentation_ops, [eval_segmentation_op])

    if FLAGS.eval_ground_truth_segmentation:
        eval_ground_seg_op = operator_creator.create_segmentation_ground_eval_op(
            graph, SEGMENTED_CAMERA_NAME)
        graph.connect([carla_op], [eval_ground_seg_op])

    # This operator evaluates the temporal decay of the ground truth of
    # object detection across timestamps.
    if FLAGS.eval_ground_truth_object_detection:
        eval_ground_det_op = operator_creator.create_eval_ground_truth_detector_op(
            graph, rgb_camera_setup, DEPTH_CAMERA_NAME)
        graph.connect([carla_op], [eval_ground_det_op])

    obj_detector_ops = []
    if FLAGS.obj_detection:
        obj_detector_ops = operator_creator.create_detector_ops(graph)
        graph.connect([carla_op], obj_detector_ops)

        if FLAGS.evaluate_obj_detection:
            obstacle_accuracy_op = operator_creator.create_obstacle_accuracy_op(
                graph, rgb_camera_setup, DEPTH_CAMERA_NAME)
            graph.connect(obj_detector_ops + [carla_op],
                          [obstacle_accuracy_op])

        if FLAGS.obj_tracking:
            tracker_op = operator_creator.create_object_tracking_op(graph)
            graph.connect([carla_op] + obj_detector_ops, [tracker_op])

        if FLAGS.fusion:
            (fusion_op, fusion_verification_op) = operator_creator.create_fusion_ops(graph)
            graph.connect(obj_detector_ops + [carla_op], [fusion_op])
            graph.connect([fusion_op, carla_op], [fusion_verification_op])

    traffic_light_det_ops = []
    if FLAGS.traffic_light_det:
        traffic_light_det_ops.append(operator_creator.create_traffic_light_op(graph))
        graph.connect([carla_op], traffic_light_det_ops)

    lane_detection_ops = []
    if FLAGS.lane_detection:
        lane_detection_ops.append(operator_creator.create_lane_detection_op(graph))
    graph.connect([carla_op], lane_detection_ops)

    agent_op = None
    if FLAGS.ground_agent_operator:
        agent_op = operator_creator.create_ground_agent_op(graph)
        graph.connect([carla_op], [agent_op])
        graph.connect([agent_op], [carla_op])
    else:
        # TODO(ionel): The ERDOS agent doesn't use obj tracker and fusion.
        agent_op = operator_creator.create_erdos_agent_op(graph, DEPTH_CAMERA_NAME)
        input_ops = [carla_op] + traffic_light_det_ops + obj_detector_ops +\
                    segmentation_ops + lane_detection_ops
        graph.connect(input_ops, [agent_op])
        graph.connect([agent_op], [carla_op])

    goal_location = (234.269989014, 59.3300170898, 39.4306259155)
    goal_orientation = (1.0, 0.0, 0.22)
    waypointer_op = operator_creator.create_waypointer_op(graph, goal_location, goal_orientation)
    graph.connect([carla_op], [waypointer_op])
    graph.connect([waypointer_op], [agent_op])

    graph.execute(FLAGS.framework)


if __name__ == '__main__':
    app.run(main)
