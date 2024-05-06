import json
import tempfile
import torch
from dataset.custom import CustomDataset
from utils.box_ops import rescale_bboxes

try:
    from pycocotools.cocoeval import COCOeval
except:
    print("It seems that the COCOAPI is not installed.")


class CustomEvaluator():
    def __init__(self, cfg, data_dir, device, image_set='val', transform=None):
        # ----------------- Basic parameters -----------------
        self.image_set = image_set
        self.transform = transform
        self.device = device
        # ----------------- Metrics -----------------
        self.map = 0.
        self.ap50_95 = 0.
        self.ap50 = 0.
        # ----------------- Dataset -----------------
        self.dataset = CustomDataset(cfg, data_dir=data_dir, image_set=image_set, transform=None, is_train=False)

    @torch.no_grad()
    def evaluate(self, model):
        """
        COCO average precision (AP) Evaluation. Iterate inference on the test dataset
        and the results are evaluated by COCO API.
        Args:
            model : model object
        Returns:
            ap50_95 (float) : calculated COCO AP for IoU=50:95
            ap50 (float) : calculated COCO AP for IoU=50
        """
        model.eval()
        ids = []
        data_dict = []
        num_images = len(self.dataset)
        print('total number of images: %d' % (num_images))

        # --------------- COCO-style evaluation ---------------
        for index in range(num_images):
            if index % 500 == 0:
                print('[Eval: %d / %d]'%(index, num_images))

            # ----------- Load an image -----------
            img, img_id = self.dataset.pull_image(index)
            orig_h, orig_w, _ = img.shape

            # ----------- Data preprocess -----------
            x, _, ratio = self.transform(img)
            x = x.unsqueeze(0).to(self.device)
            
            img_id = int(img_id)
            ids.append(img_id)

            # ----------- Model inference -----------
            outputs = model(x)
            scores = outputs['scores']
            labels = outputs['labels']
            bboxes = outputs['bboxes']

            # ----------- Rescale bboxes -----------
            bboxes = rescale_bboxes(bboxes, [orig_w, orig_h], ratio)

            # ----------- Process results -----------
            for i, box in enumerate(bboxes):
                x1 = float(box[0])
                y1 = float(box[1])
                x2 = float(box[2])
                y2 = float(box[3])
                label = self.dataset.class_ids[int(labels[i])]
                
                # COCO box format: x1, y1, bw, bh
                bbox = [x1, y1, x2 - x1, y2 - y1]
                score = float(scores[i])
                 # COCO json format
                A = {"image_id":    img_id,
                     "category_id": label,
                     "bbox":        bbox,
                     "score":       score}
                data_dict.append(A)

        annType = ['segm', 'bbox', 'keypoints']

        # ------------- COCO Box detection evaluation -------------
        if len(data_dict) > 0:
            print('evaluating ......')
            cocoGt = self.dataset.coco
            _, tmp = tempfile.mkstemp()
            json.dump(data_dict, open(tmp, 'w'))
            cocoDt = cocoGt.loadRes(tmp)
            cocoEval = COCOeval(self.dataset.coco, cocoDt, annType[1])
            cocoEval.params.imgIds = ids
            cocoEval.evaluate()
            cocoEval.accumulate()
            cocoEval.summarize()

            ap50_95, ap50 = cocoEval.stats[0], cocoEval.stats[1]
            print('ap50_95 : ', ap50_95)
            print('ap50 : ', ap50)
            self.map = ap50_95
            self.ap50_95 = ap50_95
            self.ap50 = ap50

            return ap50, ap50_95
        else:
            return 0, 0

