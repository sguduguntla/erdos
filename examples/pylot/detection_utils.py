from itertools import combinations
import math
import numpy as np
from numpy.linalg import inv
from open3d import draw_geometries, read_point_cloud
import PIL.ImageDraw as ImageDraw
import PIL.ImageFont as ImageFont

from carla.sensor import Camera
from carla.image_converter import depth_to_local_point_cloud
from carla.transform import Transform


def add_bounding_box_text(draw, xmin, ymax, ymin, color, texts):
    try:
        font = ImageFont.truetype('arial.ttf', 24)
    except IOError:
        font = ImageFont.load_default()
    # Check if there's space to add text at the top of the box.
    text_str_heights = [font.getsize(text)[1] for text in texts]
    # Each display_str has a top and bottom margin of 0.05x.
    total_text_str_height = (1 + 2 * 0.05) * sum(text_str_heights)

    if ymax > total_text_str_height:
        text_bottom = ymax
    else:
        text_bottom = ymin + total_text_str_height
    # Reverse list and print from bottom to top.
    for text in texts[::-1]:
        text_width, text_height = font.getsize(text)
        margin = np.ceil(0.05 * text_height)
        draw.rectangle(
            [(xmin, text_bottom - text_height - 2 * margin),
             (xmin + text_width, text_bottom)],
            fill=color)
        draw.text(
            (xmin + margin, text_bottom - text_height - margin),
            text, fill='black', font=font)
        text_bottom -= text_height - 2 * margin


def add_bounding_box(image, corners, color='red', thickness=4, texts=None):
    draw = ImageDraw.Draw(image)
    (xmin, xmax, ymin, ymax) = corners
    draw.line([(xmin, ymax), (xmin, ymin), (xmax, ymin),
               (xmax, ymax), (xmin, ymax)], width=thickness, fill=color)
    if texts:
        add_bounding_box_text(draw, xmin, ymax, ymin, color, texts)


def compute_miou(bboxes1, bboxes2):
    bboxes1, bboxes2 = np.array(bboxes1), np.array(bboxes2)
    x11, x12, y11, y12 = np.split(bboxes1, 4, axis=1)
    x21, y21, x22, y22 = np.split(bboxes2, 4, axis=1)

    xI1 = np.maximum(x11, np.transpose(x21))
    xI2 = np.minimum(x12, np.transpose(x22))

    yI1 = np.maximum(y11, np.transpose(y21))
    yI2 = np.minimum(y12, np.transpose(y22))

    inter_area = np.maximum((xI2 - xI1), 0) * np.maximum((yI2 - yI1), 0)

    bboxes1_area = (x12 - x11) * (y12 - y11)
    bboxes2_area = (x22 - x21) * (y22 - y21)

    union = (bboxes1_area + np.transpose(bboxes2_area)) - inter_area

    return inter_area / (union+0.0001)


def map_ground_3D_transform_to_2D(image,
                                  world_transform,
                                  rgb_transform,
                                  rgb_intrinsic,
                                  rgb_img_size,
                                  obstacle_transform):
    extrinsic_mat = world_transform * rgb_transform
    pos = obstacle_transform.to_transform_pb2().location
    pos_vector = np.array([[pos.x], [pos.y], [pos.z], [1.0]])
    transformed_3d_pos = np.dot(inv(extrinsic_mat.matrix),
                                pos_vector)
    pos2d = np.dot(rgb_intrinsic, transformed_3d_pos[:3])
    pos2d = np.array([
        pos2d[0] / pos2d[2],
        pos2d[1] / pos2d[2],
        pos2d[2]])
    (img_width, img_height) = rgb_img_size
    if pos2d[2] > 0:
        x_2d = img_width - pos2d[0]
        y_2d = img_height - pos2d[1]
        if (x_2d >= 0 and x_2d < img_width and y_2d >= 0 and y_2d < img_height):
            return (x_2d, y_2d, pos2d[2])
    return None


def map_ground_bounding_box_to_2D(image,
                                  distance_img,
                                  world_transform,
                                  obstacle_transform,
                                  bounding_box,
                                  rgb_transform,
                                  rgb_intrinsic,
                                  rgb_img_size):
    (image_width, image_height) = rgb_img_size
    extrinsic_mat = world_transform * rgb_transform
    obj_transform = Transform(obstacle_transform.to_transform_pb2())
    bbox_pb2 = bounding_box.to_bounding_box_pb2()
    bbox_transform = Transform(bbox_pb2.transform)
    ext = bbox_pb2.extent

    # 8 bounding box vertices relative to (0,0,0)
    bbox = np.array([
        [  ext.x,   ext.y,   ext.z],
        [  ext.x, - ext.y,   ext.z],
        [  ext.x,   ext.y, - ext.z],
        [  ext.x, - ext.y, - ext.z],
        [- ext.x,   ext.y,   ext.z],
        [- ext.x, - ext.y,   ext.z],
        [- ext.x,   ext.y, - ext.z],
        [- ext.x, - ext.y, - ext.z]
    ])

    # Transform the vertices with respect to the bounding box transform.
    bbox = bbox_transform.transform_points(bbox)

    # The bounding box transform is with respect to the object transform.
    # Transform the points relative to its transform.
    bbox = obj_transform.transform_points(bbox)

    # Object's transform is relative to the world. Thus, the bbox contains
    # the 3D bounding box vertices relative to the world.

    coords = []
    for vertex in bbox:
        pos_vector = np.array([
            [vertex[0,0]],  # [[X,
            [vertex[0,1]],  #   Y,
            [vertex[0,2]],  #   Z,
            [1.0]           #   1.0]]
        ])
        # Transform the points to camera.
        transformed_3d_pos = np.dot(inv(extrinsic_mat.matrix), pos_vector)
        # Transform the points to 2D.
        pos2d = np.dot(rgb_intrinsic, transformed_3d_pos[:3])

        # Normalize the 2D points.
        pos2d = np.array([
            pos2d[0] / pos2d[2],
            pos2d[1] / pos2d[2],
            pos2d[2]
        ])

        # Add the points to the image.
        if pos2d[2] > 0: # If the point is in front of the camera.
            x_2d = float(image_width - pos2d[0])
            y_2d = float(image_height - pos2d[1])
            if (x_2d >= 0 or y_2d >= 0) and (x_2d < image_width or y_2d < image_height):
                coords.append((x_2d, y_2d, pos2d[2]))

    return coords


def point_cloud_from_rgbd(depth_frame, depth_transform, world_transform):
    far = 1.0
    point_cloud = depth_to_local_point_cloud(
        depth_frame, color=None, max_depth=far)
    car_to_world_transform = world_transform * depth_transform
    point_cloud.apply_transform(car_to_world_transform)
    # filename = './point_cloud_tmp.ply'
    # point_cloud.save_to_disk(filename)
    # pcd = read_point_cloud(filename)
    # draw_geometries([pcd])
    return point_cloud


def get_3d_world_position(x, y, (image_width, image_height), depth_frame, depth_transform, world_transform):
    pc = point_cloud_from_rgbd(depth_frame,
                               depth_transform,
                               world_transform)
    return pc.array.tolist()[y * image_width + x]


def load_coco_labels(labels_path):
    labels_map = {}
    with open(labels_path) as labels_file:
        labels = labels_file.read().splitlines()
        index = 1
        for label in labels:
            labels_map[index] = label
            index += 1
    return labels_map


def get_bounding_box_from_corners(corners):
    """
    Gets the bounding box of the pedestrian given the corners of the plane.
    """
    # Figure out the opposite ends of the rectangle. Our 2D mapping doesn't
    # return perfect rectangular coordinates and also doesn't return them
    # in clockwise order.
    max_distance = 0
    opp_ends = None
    for (a, b) in combinations(corners, r=2):
        if abs(a[0] - b[0]) <= 0.8 or abs(a[1] - b[1]) <= 0.8:
            # The points are too close. They may be lying on the same axis.
            # Move forward.
            pass
        else:
            # The points possibly lie on different axis. Choose the two
            # points which are the farthest.
            distance = (b[0] - a[0])**2 + (b[1] - a[1])**2
            if distance > max_distance:
                max_distance = distance
                if a[0] < b[0] and a[1] < b[1]:
                    opp_ends = (a, b)
                else:
                    opp_ends = (b, a)

    # If we were able to find two points far enough to be considered as
    # possible bounding boxes, return the results.
    return opp_ends


def get_bounding_box_sampling_points(ends):
    """
    Get the sampling points given the ends of the rectangle.
    """
    a, b = ends

    # Find the middle point of the rectangle, and see if the points
    # around it are visible from the camera.
    middle_point = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2,
                    b[2].flatten().item(0))
    sampling_points = [
        middle_point,
        (middle_point[0] + 2, middle_point[1], middle_point[2]),
        (middle_point[0] + 1, middle_point[1] + 1, middle_point[2]),
        (middle_point[0] + 1, middle_point[1] - 1, middle_point[2]),
        (middle_point[0] - 2, middle_point[1], middle_point[2]),
        (middle_point[0] - 1, middle_point[1] + 1, middle_point[2]),
        (middle_point[0] - 1, middle_point[1] - 1, middle_point[2])
    ]
    return (middle_point, sampling_points)


def have_same_depth(x, y, z, depth_array, threshold):
    x, y = int(x), int(y)
    return abs(depth_array[y][x] * 1000 - z) < threshold


def inside_image(x, y, img_width, img_height):
    return x >= 0 and y >= 0 and x < img_width and y < img_height


def select_max_bbox(ends):
    (xmin, ymin) = tuple(map(int, ends[0][:2]))
    (xmax, ymax) = tuple(map(int, ends[0][:2]))
    corner = tuple(map(int, ends[1][:2]))
    # XXX(ionel): This is not quite correct. We get the
    # minimum and maximum x and y values, but these may
    # not be valid points. However, it works because the
    # bboxes are parallel to x and y axis.
    xmin = min(xmin, corner[0])
    ymin = min(ymin, corner[1])
    xmax = max(xmax, corner[0])
    ymax = max(ymax, corner[1])
    return (xmin, xmax, ymin, ymax)


def get_2d_bbox_from_3d_box(
        rgb_img, depth_array, world_transform, obj_transform,
        bounding_box, rgb_transform, rgb_intrinsic, rgb_img_size,
        middle_depth_threshold, neighbor_threshold):
    corners = map_ground_bounding_box_to_2D(
        rgb_img, depth_array, world_transform, obj_transform,
        bounding_box, rgb_transform, rgb_intrinsic,
        rgb_img_size)
    if len(corners) == 8:
        ends = get_bounding_box_from_corners(corners)
        if ends:
            (middle_point, points) = get_bounding_box_sampling_points(ends)
            # Select bounding box if the middle point in inside the frame
            # and has the same depth
            if (inside_image(middle_point[0], middle_point[1],
                             rgb_img_size[0], rgb_img_size[1]) and
                have_same_depth(middle_point[0],
                                middle_point[1],
                                middle_point[2],
                                depth_array,
                                middle_depth_threshold)):
                (xmin, xmax, ymin, ymax) = select_max_bbox(ends)
                width = xmax - xmin
                height = ymax - ymin
                # Filter out the small bounding boxes (they're far away).
                # We use thresholds that are proportional to the image size.
                if (width > rgb_img_size[0] * 0.01 and
                    height > rgb_img_size[1] * 0.01 and
                    width * height > rgb_img_size[0] * rgb_img_size[1] * 0.0002):
                    return (xmin, xmax, ymin, ymax)
            else:
                # The mid point doesn't have the same depth. It can happen
                # for valid boxes when the mid point is between the legs.
                # In this case, we check that a fraction of the neighbouring
                # points have the same depth.
                # Filter the points inside the image.
                points_inside_image = [
                    (x, y, z)
                    for (x, y, z) in points if inside_image(
                            x, y, rgb_img_size[0], rgb_img_size[1])
                ]
                same_depth_points = [
                    have_same_depth(x, y, z, depth_array, neighbor_threshold)
                            for (x, y, z) in points_inside_image
                ]
                if len(same_depth_points) > 0 and \
                        same_depth_points.count(True) >= 0.4 * len(same_depth_points):
                    (xmin, xmax, ymin, ymax) = select_max_bbox(ends)
                    width = xmax - xmin
                    height = ymax - ymin
                    width = xmax - xmin
                    height = ymax - ymin
                    # Filter out the small bounding boxes (they're far away).
                    # We use thresholds that are proportional to the image size.
                    if (width > rgb_img_size[0] * 0.01 and
                        height > rgb_img_size[1] * 0.01 and
                        width * height > rgb_img_size[0] * rgb_img_size[1] * 0.0002):
                        return (xmin, xmax, ymin, ymax)


def get_camera_intrinsic_and_transform(name,
                                       postprocessing,
                                       field_of_view=90.0,
                                       image_size=(800, 600),
                                       position=(2.0, 0.0, 1.4),
                                       rotation_pitch=0,
                                       rotation_roll=0,
                                       rotation_yaw=0):
    camera = Camera(
        name,
        PostProcessing=postprocessing,
        FOV=field_of_view,
        ImageSizeX=image_size[0],
        ImageSizeY=image_size[1],
        PositionX=position[0],
        PositionY=position[1],
        PositionZ=position[2],
        RotationPitch=rotation_pitch,
        RotationRoll=rotation_roll,
        RotationYaw=rotation_yaw)

    image_width = image_size[0]
    image_height = image_size[1]
    # (Intrinsic) K Matrix
    intrinsic_mat = np.identity(3)
    intrinsic_mat[0][2] = image_width / 2
    intrinsic_mat[1][2] = image_height / 2
    intrinsic_mat[0][0] = intrinsic_mat[1][1] = image_width / (2.0 * math.tan(90.0 * math.pi / 360.0))
    return (intrinsic_mat, camera.get_unreal_transform(), (image_width, image_height))

def calculate_iou(ground_truth, prediction):
    """Calculate the IoU of a single predicted ground truth box."""
    x1_gt, x2_gt, y1_gt, y2_gt = ground_truth
    x1_p, x2_p, y1_p, y2_p = prediction

    if x1_p > x2_p or y1_p > y2_p:
        raise AssertionError("Prediction box is malformed? {}".format(prediction))

    if x1_gt > x2_gt or y1_gt > y2_gt:
        raise AssertionError("Ground truth box is malformed? {}".format(ground_truth))

    if x2_gt < x1_p or x2_p < x1_gt or y2_gt < y1_p or y2_p < y1_gt:
        return 0.0

    inter_x1 = max([x1_gt, x1_p]) 
    inter_x2 = min([x2_gt, x2_p])

    inter_y1 = max([y1_gt, y1_p])
    inter_y2 = min([y2_gt, y2_p])

    inter_area = (inter_x2 - inter_x1 + 1) * (inter_y2 - inter_y1 + 1)
    gt_area = (x2_gt - x1_gt + 1) * (y2_gt - y1_gt + 1)
    pred_area = (x2_p - x1_p + 1) * (y2_p - y1_p + 1)
    return float(inter_area) / (gt_area + pred_area - inter_area)

def get_prediction_results(ground_truths, predictions, iou_threshold):
    """Calculate the number of true positives, false positives and false
    negatives from the given ground truth and predictions."""
    true_pos, false_pos, false_neg = None, None, None

    # If there are no predictions, then everything is a false negative.
    if len(predictions) == 0:
        true_pos, false_pos = 0, 0
        false_neg = len(ground_truths)
        return true_pos, false_pos, false_neg

    # If there is no ground truth, everything is a false positive.
    if len(ground_truths) == 0:
        true_pos, false_neg = 0, 0
        false_pos = len(predictions)
        return true_pos, false_pos, false_neg

    # Iterate over the predictions and calculate the IOU of each prediction
    # with each ground truth.
    ious = []
    for i, prediction in enumerate(predictions):
        for j, ground_truth in enumerate(ground_truths):
            iou = calculate_iou(prediction, ground_truth)
            if iou > iou_threshold:
                ious.append((i, j, iou))

    # If no IOUs were over the threshold, return all predictions as false
    # positives and all ground truths as false negatives.
    if len(ious) == 0:
        true_pos = 0
        false_pos, false_neg = len(predictions), len(ground_truths)
    else:
        # Sort the IOUs and match each box only once.
        ground_truths_matched, predictions_matched = set(), set()
        matched = []
        for prediction, ground_truth, iou in sorted(ious, key=lambda x: x[-1], reverse=True):
            if ground_truth not in ground_truths_matched and prediction not in predictions_matched:
                ground_truths_matched.add(ground_truth)
                predictions_matched.add(prediction)
                matched.append((prediction, ground_truth, iou))
        
        # The matches are the true positives.
        true_pos = len(matched)
        # The unmatched predictions are the false positives.
        false_pos = len(predictions) - len(predictions_matched)
        # The umatched ground truths are the false negatives.
        false_neg = len(ground_truths) - len(ground_truths_matched)

    return true_pos, false_pos, false_neg

def get_precision_recall(true_positives, false_positives, false_negatives):
    precision, recall = None, None
    if true_positives + false_positives == 0:
        precision = 0.0
    else:
        precision = true_positives / (true_positives + false_positives)

    if true_positives + false_negatives == 0:
        recall = 0.0
    else:
        recall = true_positives / (true_positives + false_negatives)

    return (precision, recall)

def get_avg_precision_at_iou(ground_truths, predictions, iou_threshold):
    true_pos, false_pos, false_neg = get_prediction_results(ground_truths,
            predictions, iou_threshold)
    precision, recall = get_precision_recall(true_pos, false_pos, false_neg)
    # Ideally, you would like to take multiple precision values at different
    # recalls and average them, but we can't vary model confidence, so we just
    # return the actual precision.
    return precision
