def _get_total_spm(self, normalized_data):
    total_spm = 0

    for pump in ["MP1_SPM", "MP2_SPM", "MP3_SPM", "MP4_SPM", "MP5_SPM"]:
        value = normalized_data.get(pump)

        if value is not None:
            total_spm += value

    return total_spm