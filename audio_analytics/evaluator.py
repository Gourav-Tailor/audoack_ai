import json
import os

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score


def evaluate_predictions_against_ground_truth(
    predictions: list[dict], ground_truths: dict
) -> dict:
    """Compares model predictions against ground truth extracted from labels.csv.

    Ground truth and prediction filenames are matched by their base name
    (path and extension stripped, case-insensitive), so "Clip1.wav" in
    predictions will correctly match "clip1.mp3" in ground_truths.

    :param predictions: List of predicted result dicts from AudioAnalysis models.
    :param ground_truths: Dict mapping filename -> ground_truth_json string.
    :return: Summary metrics dict including Macro F1, accuracy, and confusion matrix.
    """
    # Normalize ground truth keys by base filename without extension, and
    # parse each entry's JSON once up front (invalid entries are skipped).
    normalized_gt = {}
    for filename, gt_str in ground_truths.items():
        base_name = os.path.splitext(os.path.basename(filename))[0].lower()
        try:
            normalized_gt[base_name] = (
                json.loads(gt_str) if isinstance(gt_str, str) else gt_str
            )
        except (json.JSONDecodeError, TypeError):
            continue

    y_true_tone = []
    y_pred_tone = []

    y_true_noise = []
    y_pred_noise = []

    for pred in predictions:
        pred_filename = pred.get("filename", "")
        base_name = os.path.splitext(os.path.basename(pred_filename))[0].lower()

        if base_name not in normalized_gt:
            continue

        gt_data = normalized_gt[base_name]

        # Tone tracking
        y_true_tone.append(
            str(gt_data.get("emotional_tone", "neutral")).lower().strip()
        )
        y_pred_tone.append(str(pred.get("emotional_tone", "neutral")).lower().strip())

        # Background noise tracking
        y_true_noise.append(bool(gt_data.get("background_noise_present", False)))
        y_pred_noise.append(bool(pred.get("background_noise_present", False)))

    if not y_true_tone:
        return {"error": "No matching ground truth filenames found in labels.csv."}

    # Tone classification metrics
    labels = ["neutral", "satisfied", "frustrated", "upset", "distressed"]
    tone_macro_f1 = f1_score(
        y_true_tone, y_pred_tone, labels=labels, average="macro", zero_division=0
    )
    cm = confusion_matrix(y_true_tone, y_pred_tone, labels=labels)

    # Noise detection accuracy (as a percentage)
    noise_acc = np.mean(np.array(y_true_noise) == np.array(y_pred_noise)) * 100

    return {
        "emotional_tone_macro_f1": round(float(tone_macro_f1), 4),
        "noise_detection_accuracy": round(float(noise_acc), 2),
        "tone_confusion_matrix": cm.tolist(),
        "tone_labels": labels,
        "sample_count": len(y_true_tone),
    }
