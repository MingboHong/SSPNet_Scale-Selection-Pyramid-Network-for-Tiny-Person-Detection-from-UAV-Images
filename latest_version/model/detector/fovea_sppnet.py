import cv2
import numpy as np
from ..builder import DETECTORS
import torch
import os
import warnings
from mmdet.core import bbox2result
from ..builder import DETECTORS, build_backbone, build_head, build_neck
from .base import BaseDetector
# from mmdet.models.module.heatmap import Heatmap


from mmdet.models.module.tinymap import Heatmap


class SingleStageDetector(BaseDetector):
    """Base class for single-stage detectors.

    Single-stage detectors directly and densely predict bounding boxes on the
    output features of the backbone+neck.
    """

    def __init__(self,
                 backbone,
                 neck=None,
                 bbox_head=None,
                 aug_label=False,
                 train_cfg=None,
                 test_cfg=None,
                 pretrained=None,
                 init_cfg=None, ):
        super(SingleStageDetector, self).__init__(init_cfg)
        if pretrained:
            warnings.warn('DeprecationWarning: pretrained is deprecated, '
                          'please use "init_cfg" instead')
            backbone.pretrained = pretrained
        self.backbone = build_backbone(backbone)
        if neck is not None:
            self.neck = build_neck(neck)
        bbox_head.update(train_cfg=train_cfg)
        bbox_head.update(test_cfg=test_cfg)
        self.bbox_head = build_head(bbox_head)
        self.train_cfg = train_cfg
        self.test_cfg = test_cfg
        self.aug_label = aug_label
        self.att_loss = Heatmap()

    def extract_feat(self, img):
        """Directly extract features from the backbone+neck."""
        x = self.backbone(img)
        if self.with_neck:
            x, att = self.neck(x)  # , seg, reg
            return x, att
        return x

    def forward_dummy(self, img):
        """Used for computing network flops.

        See `mmdetection/tools/analysis_tools/get_flops.py`
        """
        x = self.extract_feat(img)
        outs = self.bbox_head(x)
        return outs

    def forward_train(self,
                      img,
                      img_metas,
                      gt_bboxes,
                      gt_labels,
                      gt_bboxes_ignore=None):
        """
        Args:
            img (Tensor): Input images of shape (N, C, H, W).
                Typically these should be mean centered and std scaled.
            img_metas (list[dict]): A List of image info dict where each dict
                has: 'img_shape', 'scale_factor', 'flip', and may also contain
                'filename', 'ori_shape', 'pad_shape', and 'img_norm_cfg'.
                For details on the values of these keys see
                :class:`mmdet.datasets.pipelines.Collect`.
            gt_bboxes (list[Tensor]): Each item are the truth boxes for each
                image in [tl_x, tl_y, br_x, br_y] format.
            gt_labels (list[Tensor]): Class indices corresponding to each box
            gt_bboxes_ignore (None | list[Tensor]): Specify which bounding
                boxes can be ignored when computing the loss.

        Returns:
            dict[str, Tensor]: A dictionary of loss components.
        """
        super(SingleStageDetector, self).forward_train(img, img_metas)
        x, att = self.extract_feat(img)

        gt_reg = self.att_loss.target(att, gt_bboxes)
        loss_att = self.att_loss.loss(reg_pred=att, reg_gt=gt_reg)

        losses = dict()
        losses.update(loss_att)
        losses.update(self.bbox_head.forward_train(x, img_metas, gt_bboxes,
                                                   gt_labels, gt_bboxes_ignore))
        return losses

    def simple_test(self, img, img_metas, rescale=False):
        """Test function without test-time augmentation.

        Args:
            img (torch.Tensor): Images with shape (N, C, H, W).
            img_metas (list[dict]): List of image information.
            rescale (bool, optional): Whether to rescale the results.
                Defaults to False.

        Returns:
            list[list[np.ndarray]]: BBox results of each image and classes.
                The outer list corresponds to each image. The inner list
                corresponds to each class.
        """
        # feat = self.extract_feat(img)
        combos = self.extract_feat(img)
        feat, att = combos[0], combos[1]
        # feat = [lvl[0] for lvl in combos]
        # att = [lvl[1] for lvl in combos]

        if not os.path.exists("./att"):
            os.mkdir("./att")
        for i in range(len(att)):
            att_rgb = att[i].cpu().numpy()
            att_rgb = np.power(att_rgb, 30)*255
            att_rgb = att_rgb[0].astype(np.uint8).transpose(1, 2, 0)
            att_rgb = cv2.applyColorMap(att_rgb, cv2.COLORMAP_JET)
            cv2.imwrite("./att/{}_{}".format(i, img_metas[0]['filename'].split('/')[-1]), att_rgb)
        results_list = self.bbox_head.simple_test(
            feat, img_metas, rescale=rescale)
        bbox_results = [
            bbox2result(det_bboxes, det_labels, self.bbox_head.num_classes)
            for det_bboxes, det_labels in results_list
        ]
        return bbox_results

    def aug_test(self, imgs, img_metas, rescale=False):
        """Test function with test time augmentation.

        Args:
            imgs (list[Tensor]): the outer list indicates test-time
                augmentations and inner Tensor should have a shape NxCxHxW,
                which contains all images in the batch.
            img_metas (list[list[dict]]): the outer list indicates test-time
                augs (multiscale, flip, etc.) and the inner list indicates
                images in a batch. each dict has image information.
            rescale (bool, optional): Whether to rescale the results.
                Defaults to False.

        Returns:
            list[list[np.ndarray]]: BBox results of each image and classes.
                The outer list corresponds to each image. The inner list
                corresponds to each class.
        """
        assert hasattr(self.bbox_head, 'aug_test'), \
            f'{self.bbox_head.__class__.__name__}' \
            ' does not support test-time augmentation'

        combos = self.extract_feats(imgs)
        feats = [lvl[0] for lvl in combos]
        att = [lvl[1] for lvl in combos]

        if not os.path.exists("./att"):
            os.mkdir("./att")
        for num in range(len(att)):
            for lvl in range(len(att[0])):
                att_rgb = att[num][lvl].cpu().numpy()[0]
                att_rgb = np.power(att_rgb, 15)*255
                att_rgb = att_rgb.astype(np.uint8).transpose(1, 2, 0)
                att_rgb = cv2.applyColorMap(att_rgb, cv2.COLORMAP_JET)
                cv2.imwrite("./att/{}_{}".format(lvl, img_metas[num][0]['filename'].split('/')[-1]), att_rgb)
        if self.aug_label:
            results_list = self.bbox_head.aug_test(feats, img_metas, att=att, rescale=rescale)
        else:
            results_list = self.bbox_head.aug_test(feats, img_metas, rescale=rescale)
        bbox_results = [
            bbox2result(det_bboxes, det_labels, self.bbox_head.num_classes)
            for det_bboxes, det_labels in results_list
        ]
        return bbox_results

    def onnx_export(self, img, img_metas, with_nms=True):
        """Test function without test time augmentation.

        Args:
            img (torch.Tensor): input images.
            img_metas (list[dict]): List of image information.

        Returns:
            tuple[Tensor, Tensor]: dets of shape [N, num_det, 5]
                and class labels of shape [N, num_det].
        """
        x = self.extract_feat(img)
        outs = self.bbox_head(x)
        # get origin input shape to support onnx dynamic shape

        # get shape as tensor
        img_shape = torch._shape_as_tensor(img)[2:]
        img_metas[0]['img_shape_for_onnx'] = img_shape
        # get pad input shape to support onnx dynamic shape for exporting
        # `CornerNet` and `CentripetalNet`, which 'pad_shape' is used
        # for inference
        img_metas[0]['pad_shape_for_onnx'] = img_shape

        if len(outs) == 2:
            # add dummy score_factor
            outs = (*outs, None)
        # TODO Can we change to `get_bboxes` when `onnx_export` fail
        det_bboxes, det_labels = self.bbox_head.onnx_export(
            *outs, img_metas, with_nms=with_nms)

        return det_bboxes, det_labels


@DETECTORS.register_module()
class FOVEA_SPPNet(SingleStageDetector):
    """Implementation of `FoveaBox <https://arxiv.org/abs/1904.03797>`_"""

    def __init__(self,
                 backbone,
                 neck,
                 bbox_head,
                 aug_label=False,
                 train_cfg=None,
                 test_cfg=None,
                 pretrained=None,
                 ):
        super(FOVEA_SPPNet, self).__init__(backbone, neck, bbox_head, aug_label, train_cfg,
                                           test_cfg, pretrained)
