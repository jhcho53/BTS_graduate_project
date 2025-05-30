import os
import glob
import json
import cv2
import torch
from torch.utils.data import Dataset

class MultiVideoAnomalyDataset(Dataset):
    """
    Multiple-video anomaly detection Dataset that reads per-file ROI from JSON.
    Args:
        label_dir (str): JSON 파일들이 위치한 디렉토리
        seq_len   (int): 연속 프레임 수
        default_roi (tuple): JSON에 ROI가 없을 때 사용할 기본 ROI
        transform (callable, optional): 반환 시퀀스에 적용할 변환
    """
    def __init__(self, label_dir, seq_len=8,
                 default_roi=(1150, 300, 1600, 700),
                 transform=None):
        super().__init__()
        self.seq_len     = seq_len
        self.default_roi = default_roi
        self.transform   = transform
        self.samples     = []  # list of (video_path, start_idx, label, roi)

        json_paths = glob.glob(os.path.join(label_dir, '*.json'))
        project_dir = os.path.dirname(label_dir)
        video_dir   = os.path.join(project_dir, 'video')

        for ann_path in json_paths:
            # JSON 로드
            with open(ann_path, 'r') as f:
                item = json.load(f)

            # ROI 정보 불러오기 (없으면 default_roi)
            roi = tuple(item['annotations'].get('roi', self.default_roi))

            # 이벤트 프레임 인덱스 목록
            anomaly_frames = [
                (int(s), int(e))
                for s, e in item['annotations']['event_frame']
            ]

            # 비디오 경로 유도
            base = os.path.splitext(os.path.basename(ann_path))[0]
            vpath = os.path.join(video_dir, base + '.mp4')

            # 총 프레임 수 조회
            cap = cv2.VideoCapture(vpath)
            nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            # 슬라이딩 윈도우 샘플 생성
            for start in range(0, nframes - seq_len + 1):
                end = start + seq_len - 1
                label = 0.0
                for s, e in anomaly_frames:
                    if not (end < s or start > e):
                        label = 1.0
                        break
                # roi도 함께 저장
                self.samples.append((vpath, start, label, roi))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        vpath, start, label, roi = self.samples[idx]
        x1, y1, x2, y2 = roi

        cap = cv2.VideoCapture(vpath)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)

        frames = []
        for _ in range(self.seq_len):
            ret, img = cap.read()
            if not ret:
                break
            # per-sample ROI 적용
            crop  = img[y1:y2, x1:x2]
            patch = cv2.resize(crop, (256, 256))
            patch = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
            t = torch.from_numpy(patch).permute(2,0,1).float().div(255.0)
            frames.append(t)
        cap.release()

        seq = torch.stack(frames, dim=0)
        if self.transform:
            seq = self.transform(seq)
        return seq, torch.tensor(label, dtype=torch.float)



import os
import json
import cv2
import torch
from torch.utils.data import Dataset

class SingleVideoAnomalyDataset(Dataset):
    """
    Map-style Dataset for a single video anomaly detection using one JSON annotation with ROI cropping.

    Args:
        ann_path (str): Path to the JSON annotation file (e.g., '/.../label/E01_001.json').
        seq_len  (int): Number of consecutive frames per sequence.
        fps      (int): Frames per second rate (unused for frame-index labels).
        roi      (tuple): (x1, y1, x2, y2) Region of interest to crop in each frame.
        transform (callable, optional): Transform applied to each returned sequence tensor.
    """
    def __init__(self, ann_path, seq_len=8, fps=8,
                 roi=(1150, 300, 1600, 700), transform=None):
        super().__init__()
        self.seq_len = seq_len
        self.fps = fps  # fps는 event_frame이 초단위가 아닐 경우에만 사용
        self.transform = transform
        self.roi = roi  # (x1, y1, x2, y2)
        self.samples = []  # List of (video_path, start_frame_idx, label)

        # Load JSON annotation
        with open(ann_path, 'r') as f:
            item = json.load(f)

        # Derive video path from annotation path
        label_dir   = os.path.dirname(ann_path)
        project_dir = os.path.dirname(label_dir)
        video_dir   = os.path.join(project_dir, 'video')
        base_name   = os.path.splitext(os.path.basename(ann_path))[0]
        video_path  = os.path.join(video_dir, base_name + '.mp4')

        # Get total frame count
        cap = cv2.VideoCapture(video_path)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        # event_frame 필드는 프레임 인덱스 범위를 직접 제공
        anomaly_frames = []
        for start_idx, end_idx in item['annotations']['event_frame']:
            s = int(start_idx)
            e = int(end_idx)
            anomaly_frames.append((s, e))

        # Generate sliding window samples
        for start in range(0, frame_count - seq_len + 1):
            end = start + seq_len - 1
            label = 0.0
            for s, e in anomaly_frames:
                if not (end < s or start > e):
                    label = 1.0
                    break
            self.samples.append((video_path, start, label))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        video_path, start_idx, label = self.samples[idx]
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_idx)

        frames = []
        x1, y1, x2, y2 = self.roi
        for _ in range(self.seq_len):
            ret, frame = cap.read()
            if not ret:
                break
            # Crop ROI
            crop = frame[y1:y2, x1:x2]
            # Resize to 256×256
            patch = cv2.resize(crop, (256, 256))
            # Convert BGR→RGB and to tensor
            patch = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
            tensor = torch.from_numpy(patch).permute(2, 0, 1).float() / 255.0
            frames.append(tensor)
        cap.release()

        seq = torch.stack(frames, dim=0)  # (T, C, 256, 256)
        if self.transform:
            seq = self.transform(seq)
        return seq, torch.tensor(label, dtype=torch.float)
