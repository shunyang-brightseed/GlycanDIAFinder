from collections import defaultdict
import os
from statistics import mean

import csv
from collections import defaultdict
from configparser import ConfigParser
import timeit

from matchms.importing import load_from_mzxml
from matchms import set_matchms_logger_level
import numpy as np
from scipy.ndimage import gaussian_filter
# from scipy.signal import savgol_filter
from scipy.signal import peak_prominences
import matplotlib.pyplot as plt
from scipy.signal import find_peaks
from collections import defaultdict
# import argparse
import gc

class Arguments():
    def __init__(self, input_fn, output_fd, ms2_mass_list, min_height, threshold, charge, charge_range, polarity, ppm_ms1, ppm_ms2, cpd, adduct, addon_mass, min_matched_cnt_ms2, note, min_mass, max_mass, min_rt, max_rt, flex_mode, debug_mode, max_aligned_record_ms2):
        """Align peaks of MS1 data and MS2 data

        Parameters
        ----------
        input_fn : str
            input file name
        output_path : str
            output path
        ms2_mass_list : list of float
            mass list in ms2
        min_height : float
            minimum height of the peaks. Any peaks with lower intensity will be filtered.
        threshold : float
            threshold * max intensity among peaks. Any peaks with lower intensity will be filtered
        charge : int
            max charge
        charge_range : str
            the range of charge to be searched
        polarity : str
            polarity of the search. choices=["positive", "negative"]
        ppm_ms1 : float
            ppm of ms1 dfs
        ppm_ms2 : float
            ppm of ms2 dfs
        cpd : str
            Glycan structure: a_b_c_d_e, in which e is usually 0 unless specified
        adduct : str
            type of adduct: choices=["H", "Na", "K", "NH4"]
        addon_mass : str
            add on mass
        min_matched_cnt_ms2 : int
            minimum matched count in ms2
        note : str
            Note of the cpd
        min_mass : float
            minimum mass of calcualted df1
        max_mass : float
            maximum mass of calcualted df1
        min_rt : float
            minimum retention time of be searched
        max_rt : float
            maximum rentention time to be searched
        flex_mode : str
            flexibile mode where only mass1 in ms1 will be checked
        debug_mode : bool
            if exists, print info for debugging.
        max_aligned_record_ms2 : int
            maximum aligned MS2 peak number recored for stats
        

        """
        self.input_fn = input_fn
        self.output_path = output_fd
        self.ms2_mass_list = list(map(float, ms2_mass_list.split(" ")))
        self.min_height = float(min_height)
        self.threshold = float(threshold)
        self.charge = int(charge)
        self.charge_range = charge_range
        self.polarity = polarity
        self.ppm_ms1 = float(ppm_ms1)
        self.ppm_ms2 = float(ppm_ms2)
        self.cpd = cpd
        self.adduct = adduct
        self.addon_mass = addon_mass
        self.min_matched_cnt_ms2 = int(min_matched_cnt_ms2)
        self.note = note
        self.min_mass = float(min_mass)
        self.max_mass = float(max_mass)
        self.min_rt = float(min_rt)
        self.max_rt = float(max_rt)
        self.flex_mode = flex_mode
        self.debug_mode = bool(debug_mode)
        self.max_aligned_record_ms2 = max_aligned_record_ms2 # format has been converted previously

        assert(polarity in ["positive", "negative"])
        assert(adduct in ["H", "Na", "K", "NH4"])



def creat_path(path):
    """Creat path+folder if not exist. Do nothing if path exists

    Parameters
    ----------
    path : str
        path + folder_name
    """
    isExists = os.path.exists(path)

    if not isExists:
        os.makedirs(path)
        print("path generated:", path)
    else:
        print("path exists:", path)

def find_filter_peaks(args, rt_list, intensity_list, sigma=1.0, min_height=0, threshold=0, delta=0.2):
    """find the peaks in rt-intensity waveform and filter the invalid peaks.

    Invalid peaks means their intensify/baseline < 3

    Parameters
    ----------
    rt_list : list
        list of retention time
    intensity_list : list
        Intensity list of a df
    sigma : float, optional
        sigma used in Gaussian filter, by default 1.0
    min_height : float, optional
        minimum peaks' intensity, by default 0
    threshold : float, optional
        portion of minimum peaks' intensity to the maximum peaks' intensity, by default 0. Ranges: 0-1

    Returns
    -------
    peak_idx_list2 : list
        Valid peaks found in the waveform
    intensity_filt_arr : np.narray
        Filtered intensity array
    peak_baseline_list : list

    """

    # Apply a Gaussian filter with sigma=1.0
    intensity_filt_arr = gaussian_filter(intensity_list, sigma=sigma)

    # find the max intensity and min intensity used to filter some peaks
    max_intensity = intensity_filt_arr.max()
    min_intensity = max(min_height, threshold * max_intensity)

    if args.debug_mode:
        print("max_intensity", max_intensity)
        print("min_intensity:", min_intensity)

    # find all the peaks
    peak_idx_arr, find_peak_prop_dict = find_peaks(intensity_filt_arr)

    if args.debug_mode:
        print("indices of all the peaks::", peak_idx_arr)
        print("find_peak_prop_dict:", find_peak_prop_dict)

    # find prominance to find left and right baselines
    # base_arr is idx array
    prom_rst_arr, prom_l_base_arr, prom_r_base_arr = peak_prominences(intensity_filt_arr, peak_idx_arr)

    # fiter invalid peaks using left and right baselines
    peak_idx_list2 = []
    peak_baseline_list = []
    for i, peak_idx in enumerate(peak_idx_arr):
        left_baseline = intensity_filt_arr[prom_l_base_arr[i]]
        right_baseline = intensity_filt_arr[prom_r_base_arr[i]]
        max_baseline = max(left_baseline, right_baseline)
        peakIntensity_maxBaseline_ratio = intensity_filt_arr[peak_idx] / max_baseline

        if peakIntensity_maxBaseline_ratio >= 2 and intensity_filt_arr[peak_idx] >= min_intensity:
            peak_idx_list2.append(peak_idx)
            peak_baseline_list.append(max_baseline)

    # print("peak_idx_list2:", peak_idx_list2)

    # check the distance of adjacent peaks
    peak_idx_list3 = check_peaks_distance(args, peak_idx_list2, rt_list, intensity_filt_arr, delta)

    return peak_idx_list3, intensity_filt_arr, peak_baseline_list

def check_peaks_distance(args, peak_idx_list, rt_list, intensity_filt_arr, delta=0.2):
    """Check the distance of peaks. if it is too close, remove the peak with smaller intensity

    Parameters
    ----------
    peak_idx_list : list
        The list of peak indices
    rt_list : list
        list of retention time
    intensity_filt_arr : np.narray
        Numpy array of the filtered intensity
    delta : float, optional
        Minimum delta of peak rt list
    
    Returns
    -------
    peak_idx_fin_list : list
        
    """

    peak_idx_flit2_set = set(peak_idx_list)
    rt_peak_filt_arr = np.array([rt_list[peak_idx] for peak_idx in peak_idx_list])
    rt_peak1_arr = rt_peak_filt_arr[0:-1]
    rt_peak2_arr =rt_peak_filt_arr[1:]
    delta_rt_peak_arr = np.subtract(rt_peak2_arr, rt_peak1_arr)

    intensity_peak_filt_arr = np.array([intensity_filt_arr[peak_idx] for peak_idx in peak_idx_list])
    intensity_peak1_arr = intensity_peak_filt_arr[0:-1]
    intensity_peak2_arr = intensity_peak_filt_arr[1:]
    delta_intensity_peak_arr = np.subtract(intensity_peak2_arr, intensity_peak1_arr)

    if args.debug_mode:
        print(rt_peak1_arr, "\n", rt_peak2_arr)
        print("delta_rt_peak_arr", delta_rt_peak_arr)
        print("delta_intensity_peak_arr", delta_intensity_peak_arr)

    for i, delta_rt_peak in enumerate(delta_rt_peak_arr):
        if abs(delta_rt_peak) < 0.2:
            peak_idx1 = peak_idx_list[i]
            peak_idx2 = peak_idx_list[i+1]

            # rt[peak2] >= rt[peak1], keep peak2, lose peak1
            if delta_intensity_peak_arr[i] >= 0 and peak_idx1 in peak_idx_flit2_set:
                peak_idx_flit2_set.remove(peak_idx1)
            # rt[peak2] < rt[peak1], keep peak1, lose peak2
            if delta_intensity_peak_arr[i] < 0 and peak_idx2 in peak_idx_flit2_set:
                peak_idx_flit2_set.remove(peak_idx2)

    peak_idx_fin_list = list(peak_idx_flit2_set)
    peak_idx_fin_list.sort()

    # print("peak_idx_fin_list after filt2", peak_idx_fin_list, type(peak_idx_fin_list))

    return peak_idx_fin_list


def find_valid_precursor_mz(df, precMZ_spectID_dict):
    """find valid precursor_mz based on df in precMZ_specID_dict

    left_prec_mz < df <= right_prec_mz

    Parameters
    ----------
    df : float
        df in MS1 (e.g. 966.8511)
    precMZ_spectID_dict : defaultdict(list)
        A dict of MS2 file (precursor_mz : list of spectrum idx)

    Returns
    -------
    prec_mz_list : list
        A list of vaild precursor_mz 
    """

    prec_mz_list = []
    left_prec_mz = float("-inf") # the prec_mz smaller than df
    right_prec_mz = float("inf") # the prec_mz larger than df

    for prec_mz in precMZ_spectID_dict.keys():
        if prec_mz < df and prec_mz >= left_prec_mz:
            left_prec_mz = prec_mz
        elif prec_mz >= df and prec_mz <= right_prec_mz:
            right_prec_mz = prec_mz

    prec_mz_list.append(left_prec_mz)
    prec_mz_list.append(right_prec_mz)

    return prec_mz_list

def find_nearest_precursor_mz(df, precMZ_spectID_dict):

    prec_mz_list = []
    delta_min = float("inf")
    prec_mz_nearest = None

    for prec_mz in precMZ_spectID_dict.keys():
        delta = abs(prec_mz - df)
        if delta < delta_min:
            # update prec_mz_nearst and delta_min
            prec_mz_nearest = prec_mz
            delta_min = delta

    assert(prec_mz_nearest is not None)
    prec_mz_list.append(prec_mz_nearest)
    return prec_mz_list

def extract_info_ms2(df_ms2_list, delta_mz_ms2_list, spec_ms2_idx_list, spectrums_ms2_list, df_cnt_min=3):
    """extract information from specturms in ms2

    Parameters
    ----------
    df_ms2_list : list
        The list of df in MS2
    delta_mz_ms2_list : list
        The list of delta_mz in MS2
    spec_ms2_idx_list : list 
        The list of specturm idx in MS2
    spectrums_ms2_list : list
        The list of spectrum in MS2
    df_cnt_min : int
        Minimum cnt of df located in the same spectrum, otherwise this spectrum is invalid
    
    Returns
    -------
    df_intensity_dict : defaultdict(list)
        dict of intensity list of all MS2 dfs (df : list of intensity)
    df_rt_dict : defaultdict(list)
        dict of rt list of all MS2 dfs (df : list of rt)
    df_scan_num_dict : defaultdict(list)
        dict of scan_num list of all MS2 dfs (df : list of scan_num)
    """
    df_rt_dict = defaultdict(list) # df : list of rt
    df_scan_num_dict = defaultdict(list) # df : list of scan_num
    df_intensity_dict = defaultdict(list) # df : list of intensity

    for spec_ms2_idx in spec_ms2_idx_list:
        spectrum_ms2 = spectrums_ms2_list[spec_ms2_idx]

        mz_array = spectrum_ms2.peaks.mz

        # check how many df are located in the same spectrum
        # record existed df and the associated df_idx in a spectrum
        df_idx_dict = defaultdict(int) # df : df_idx
        for i, df in enumerate(df_ms2_list):
            df_idx_arr = np.where(abs(df - mz_array) <= delta_mz_ms2_list[i])[0]
            if len(df_idx_arr) != 0:
                assert(len(df_idx_arr) == 1)
                df_idx_dict[df] = df_idx_arr[0]
            # If df doesn't exit, df_idx < 0.
            else:
                df_idx_dict[df] = -100

        # if these are less than df_cnt_min df in the same spectrum, continue processing next spectrum
        if len(df_idx_dict) < df_cnt_min:
            continue

        # record rt, scan_num of this spectrum and intensity of dfs
        for df in df_idx_dict.keys():
            df_idx = df_idx_dict[df]
            if df_idx >= 0:
                df_intensity_dict[df].append(spectrum_ms2.peaks.intensities[df_idx])
                df_rt_dict[df].append(spectrum_ms2.metadata["retention_time"])
                df_scan_num_dict[df].append(int(spectrum_ms2.metadata["scan_number"]))
            # if df not exit, df_idx < 0
            else:
                # df_intensity_dict[df].append(1)
                # df_rt_dict[df].append(spectrum_ms2.metadata["retention_time"])
                # df_scan_num_dict[df].append(int(spectrum_ms2.metadata["scan_number"]))
                pass

    return df_intensity_dict, df_rt_dict, df_scan_num_dict


def find_aligned_peaks(args, df_peak_idx_ms1_list, df_peak_scan_num_ms1_list, df_peak_idx_ms2_dict, df_peak_scan_num_ms2_dict, df_peak_intensity_dict, delta_peak_scan_num=50):
    """find aligned peaks between MS1 and MS2

    Parameters
    ----------
    df_peak_idx_ms1_list : list
        List of peaks indices of df in MS1
    df_peak_scan_num_ms1_list : list
        List of peaks' scan_num of df in MS1
    df_peak_idx_ms2_dict : defaultdict(list)
        Dict of list of peaks' indices in MS2 (df : peak_idx list)
    df_peak_scan_num_ms2_dict : defaultdict(list)
        Dict of list of peaks' scan number list in MS2 (df : scan number list)
    df_peak_intensity_dict : defaultdict(list)
        Dict of list of peak's intensity in MS2 (df : intensity list)
    delta_peak_scan_num : int
        Margin of peaks' scan number between MS1 and MS2
    
    Return
    ------
    aligned_peak_ms1_2_dict : defaultdict(list)
        Dict of aligned peaks list in MS2 for all the peaks in MS1 (peak_idx in MS1 : aligned peak_idx in MS2)
    aligned_peak_tot_intenisty_ms2_dict : defaultdict(int)
        Dict of total intensity of aligned peaks in MS2 for each peak in MS1 (peak_idx in MS1 : accumulated intensity of aligned peaks in MS2 for each peak in MS1)
    """
    # df_start_ms2_dict = defaultdict(int) # Dict of start idx searching df_peak_scan_num_ms2_dict (df_ms2: searching start idx)

    # Dict of aligned peaks list in MS2 for all the peaks in MS1
    aligned_peak_ms1_2_dict = defaultdict(list) # peak_idx in MS1 : aligned peak_idx in MS2

    # Dict of total intensity of aligned peaks in MS2 for each peak in MS1
    aligned_peak_tot_intenisty_ms2_dict = defaultdict(int) # peak_idx in MS1 : total intensity of aligned peaks in MS2 for each peak in MS1

    # Dict of list of aligned peaks' intensity of MS2 data for each peak in MS1
    aligned_peak_intensity_list_dict = defaultdict(list)

    for i in range(len(df_peak_scan_num_ms1_list)):
        df_peak_scan_num_ms1 = df_peak_scan_num_ms1_list[i] 
        df_peak_idx_ms1 = df_peak_idx_ms1_list[i]

        for df_ms2 in df_peak_scan_num_ms2_dict.keys():

            # df_peak_scan_num_ms2_arr = np.array(df_peak_scan_num_ms2_dict[df_ms2])

            # print("df_peak_scan_num_ms1", df_peak_scan_num_ms1)
            # print("df_peak_scan_num_ms2_arr", df_peak_scan_num_ms2_arr)

            # aligned_relative_idx_arr = np.where(abs(df_peak_scan_num_ms1 - df_peak_scan_num_ms2_arr) <= delta_peak_scan_num)[0]

            # if len(aligned_relative_idx_arr) != 0:

            #     assert(len(aligned_relative_idx_arr) == 1)
            #     relative_peak_idx = aligned_relative_idx_arr[0]

            #     aligned_peak_ms1_2_dict[df_peak_idx_ms1].append(df_peak_idx_ms2_dict[df_ms2][relative_peak_idx])
            
            df_peak_scan_num_ms2_list = df_peak_scan_num_ms2_dict[df_ms2]
            if args.debug_mode:
                print("df_peak_scan_num_ms1", df_peak_scan_num_ms1)
                print("df_peak_scan_num_ms2_arr", df_ms2, df_peak_scan_num_ms2_list)

            for j, peak_scan_num_ms2 in enumerate(df_peak_scan_num_ms2_list):

                if abs(df_peak_scan_num_ms1 - peak_scan_num_ms2) <= delta_peak_scan_num:
                    df_peak_idx_ms2 = df_peak_idx_ms2_dict[df_ms2][j]
                    df_peak_intensity_ms2 = df_peak_intensity_dict[df_ms2][j]

                    # accumulate intensity of aligned ms2 peak
                    # aligned_peak_tot_intenisty_ms2_dict[df_peak_idx_ms1] += df_peak_intensity_ms2
                    aligned_peak_intensity_list_dict[df_peak_idx_ms1].append(df_peak_intensity_ms2)


                    aligned_peak_ms1_2_dict[df_peak_idx_ms1].append(df_peak_idx_ms2)

        # accumulate intensity of aligned ms2 peak
        aligned_peak_intensity_list = aligned_peak_intensity_list_dict[df_peak_idx_ms1]
        if len(aligned_peak_intensity_list) > args.max_aligned_record_ms2:
            aligned_peak_intensity_list.sort(reverse=True)
            aligned_peak_tot_intenisty_ms2_dict[df_peak_idx_ms1] = sum(aligned_peak_intensity_list[0:args.max_aligned_record_ms2])
        else:
            aligned_peak_tot_intenisty_ms2_dict[df_peak_idx_ms1] = sum(aligned_peak_intensity_list)


        if args.debug_mode:
           print("df_peak_idx_ms1:", df_peak_idx_ms1, len(aligned_peak_ms1_2_dict[df_peak_idx_ms1]))
    
    return aligned_peak_ms1_2_dict, aligned_peak_tot_intenisty_ms2_dict

# def add_option(parser):
#     parser.add_argument("--input_fn", type=str, help="input file name")
#     parser.add_argument("--output_path", type=str, help="output path")
#     # parser.add_argument("--ms1_df1", type=float, default=None, help="df1 in ms1")
#     # parser.add_argument("--ms1_df2", type=float, default=None, help="df2 in ms1")
#     # parser.add_argument("--ms1_df3", type=float, default=None, help="df3 in ms1")
#     parser.add_argument("--ms2_mass_list", nargs='+', type=float, help="mass list in ms2")
#     parser.add_argument("--min_height", type=float, default=5000, help="minimum height of the peaks. Any peaks with lower intensity will be filtered")
#     parser.add_argument("--threshold", type=float, default=0.001, help="minimum height = threshold * max intensity among peaks. Any peaks with lower intensity will be filtered")
#     parser.add_argument("--charge", type=int, default=3, help="max charge")
#     parser.add_argument("--charge_range", type=str, default=None, help="the range of charge to be searched")
#     parser.add_argument("--polarity", type=str, choices=["positive", "negative"], default="positive", help="polarity of the search")
#     parser.add_argument("--ppm_ms1", type=float, default=10, help="ppm of ms1 dfs")
#     parser.add_argument("--ppm_ms2", type=float, default=20, help="ppm of ms2 dfs")
#     parser.add_argument("--cpd", type=str, default=None, help="Glycan structure: a_b_c_d_e, in which e is usually 0 unless specified")
#     parser.add_argument("--adduct", type=str, choices=["H", "Na", "K", "NH4"], default="H", help="type of adduct")
#     parser.add_argument("--addon_mass", type=str, default="0", help="add on mass")
#     parser.add_argument("--min_matched_cnt_ms2", type=int, default=3, help="minimum matched count in ms2")
#     parser.add_argument("--note", type=str, default=None, help="Note of the cpd")
#     parser.add_argument("--min_mass", type=float, default=0, help="minimum mass of calcualted df1")
#     parser.add_argument("--max_mass", type=float, default=float("inf"), help="maximum mass of calcualted df1")
#     parser.add_argument("--min_rt", type=float, default=0, help="minimum retention time of be searched")
#     parser.add_argument("--max_rt", type=float, default=float("inf"), help="maximum rentention time to be searched")
#     parser.add_argument("--flex_mode", type=str, default="false", help="flexibile mode where only mass1 in ms1 will be checked")
#     parser.add_argument("--debug_mode", action="store_true", default=False, help="if exists, print info for debugging.")


def search_ms1(args, spectrums_ms1_list, z, cpd_list, adduct_mass):
    """search ms1 to calculate dfs and find associated waveforms

    Parameters
    ----------
    args : class
        Input configurate class
    spectrums_ms1_list : list
        list of ms1 spectrums
    z : int 
        the specific charge number
    cpd_list : list
        list of compound
    adduct_mass : float
        the mass of adduct
    
    Returns
    -------
    df_list : list
        the list of dfs
    rt_list : list
        retention time list (x-axis)
    scan_num_list : list
        scan number list
    df1_intensity_list : list
        df1 intensity list (y-axis)
    df2_intensity_list : list
        df2 intensity list (y-axis)
    df3_intensity_list : list
        df3 intensity list (y-axis)
    """

    # calculate the dfs
    const_list = [162.05282, 203.07937, 146.05791, 291.09542, 307.09033]

    addon_mass = float(args.addon_mass)

    if args.polarity == "positive":
        df1 = (sum(np.multiply(cpd_list, const_list)) + 18.01056 + adduct_mass * z + addon_mass) / z
        df2 = (sum(np.multiply(cpd_list, const_list)) + 18.01056 + adduct_mass * z + 1.0034 + addon_mass) / z
        df3 = (sum(np.multiply(cpd_list, const_list)) + 18.01056 + adduct_mass * z + 2.006 + addon_mass) / z
    else:
        df1 = (sum(np.multiply(cpd_list, const_list)) + 18.01056 - adduct_mass * z + addon_mass) / z
        df2 = (sum(np.multiply(cpd_list, const_list)) + 18.01056 - adduct_mass * z + 1.0034 + addon_mass) / z
        df3 = (sum(np.multiply(cpd_list, const_list)) + 18.01056 - adduct_mass * z + 2.006 + addon_mass) / z

    if args.debug_mode:
        print("df1:", df1)
        print("df2:", df2)
        print("df3:", df3)

    df_list = [df1, df2, df3]

    # calculate assosicated errors
    delta_mz1 = args.ppm_ms1 * df1 / 1e6
    delta_mz2 = args.ppm_ms1 * df2 / 1e6
    delta_mz3 = args.ppm_ms1 * df3 / 1e6

    if args.debug_mode:
        print("delta_mz1:", delta_mz1)
        print("delta_mz2:", delta_mz2)
        print("delta_mz3:", delta_mz3)


    rt_list = []
    scan_num_list = []
    df1_intensity_list = []
    df2_intensity_list = []
    df3_intensity_list = []

    # # the list of all the rt, df1_mz, df2_mz, df3_mz as long as df1_mz exists
    # rt_df1_list = []
    # df1_mz_all_list = []
    # df2_mz_all_list = []
    # df3_mz_all_list = []

    # interatively process each spectrum in SM1
    for spec_ms1_idx, spectrum_ms1 in enumerate(spectrums_ms1_list):
 
        mz_array = spectrum_ms1.peaks.mz

        # check whether df1, df2 and df3 is located in this spectrum
        df1_idx_arr = np.where(abs(df1 - mz_array) <= delta_mz1)[0]
        df2_idx_arr = np.where(abs(df2 - mz_array) <= delta_mz2)[0]
        df3_idx_arr = np.where(abs(df3 - mz_array) <= delta_mz3)[0]

        # # record rt, df1, df2, df3 as long as df1 exists 
        # # to check whether there is missing mz (only used for debugging)
        # if len(df1_idx_arr) and (len(df2_idx_arr) or len(df3_idx_arr)):
        #     # record RT
        #     rt_df1_ms1 = spectrum_ms1.metadata["retention_time"]
        #     rt_df1_list.append(rt_df1_ms1)

        #     # assert(len(df1_idx_arr) == 1)

        #     df1_idx = df1_idx_arr[0]
        #     df1_mz = spectrum_ms1.peaks.mz[df1_idx]
        #     df1_mz_all_list.append(df1_mz)

        #     if len(df2_idx_arr):
        #         df2_idx = df2_idx_arr[0]
        #         df2_mz = spectrum_ms1.peaks.mz[df2_idx]
        #         df2_mz_all_list.append(df2_mz)
        #     else:
        #         df2_mz_all_list.append("")
            
        #     if len(df3_idx_arr):
        #         df3_idx = df3_idx_arr[0]
        #         df3_mz = spectrum_ms1.peaks.mz[df3_idx]
        #         df3_mz_all_list.append(df3_mz)
        #     else:
        #         df3_mz_all_list.append("")

        if args.flex_mode == "true":
            if len(df1_idx_arr):
                # record RT
                rt_spec_ms1 = spectrum_ms1.metadata["retention_time"]
                rt_list.append(rt_spec_ms1)

                # record scan_number
                scan_num_spec_ms1 = spectrum_ms1.metadata["scan_number"]
                scan_num_list.append(int(scan_num_spec_ms1))

                if args.debug_mode:
                    print("specturm MS1 idx:", spec_ms1_idx)
                    print("spectrum RT:", rt_spec_ms1)
                    print("spectrum scan num:", scan_num_spec_ms1)

                if len(df1_idx_arr) != 1:
                    print("WARNING: %d df1 is found!" % len(df1_idx_arr), "df1:", [spectrum_ms1.peaks.mz[x] for x in df1_idx_arr])
                # for df1_idx in df1_idx_arr:
                df1_idx = df1_idx_arr[0]
                df1_intensity = spectrum_ms1.peaks.intensities[df1_idx]
                df1_intensity_list.append(df1_intensity)

                if args.debug_mode:
                    print("df1", df1_idx, spectrum_ms1.peaks.mz[df1_idx], df1_intensity)
            
            else:
                # record RT
                rt_spec_ms1 = spectrum_ms1.metadata["retention_time"]
                rt_list.append(rt_spec_ms1)

                # record scan_number
                scan_num_spec_ms1 = spectrum_ms1.metadata["scan_number"]
                scan_num_list.append(int(scan_num_spec_ms1))

                df1_intensity_list.append(1)

        else:
            # record the rt and intensity if all of the dfs exists
            if len(df1_idx_arr) and len(df2_idx_arr) and len(df3_idx_arr):
                
                # record RT
                rt_spec_ms1 = spectrum_ms1.metadata["retention_time"]
                rt_list.append(rt_spec_ms1)

                # record scan_number
                scan_num_spec_ms1 = spectrum_ms1.metadata["scan_number"]
                scan_num_list.append(int(scan_num_spec_ms1))

                if args.debug_mode:
                    print("specturm MS1 idx:", spec_ms1_idx)
                    print("spectrum RT:", rt_spec_ms1)
                    print("spectrum scan num:", scan_num_spec_ms1)

                # assert(len(df1_idx_arr) == 1)
                # assert(len(df2_idx_arr) == 1)
                # print("df3_idx_arr", df3_idx_arr)
                # print("df3:", [spectrum_ms1.peaks.mz[df3_idx] for df3_idx in df3_idx_arr])
                # assert(len(df3_idx_arr) == 1)

                if len(df1_idx_arr) != 1:
                    print("WARNING: %d df1 is found!" % len(df1_idx_arr), "df1:", [spectrum_ms1.peaks.mz[x] for x in df1_idx_arr])
                # for df1_idx in df1_idx_arr:
                df1_idx = df1_idx_arr[0]
                df1_intensity = spectrum_ms1.peaks.intensities[df1_idx]
                df1_intensity_list.append(df1_intensity)

                if args.debug_mode:
                    print("df1", df1_idx, spectrum_ms1.peaks.mz[df1_idx], df1_intensity)

                if len(df2_idx_arr) != 1:
                    print("WARNING: %d df2 is found!" % len(df2_idx_arr), "df2:", [spectrum_ms1.peaks.mz[x] for x in df2_idx_arr])

                # for df2_idx in df2_idx_arr:
                df2_idx = df2_idx_arr[0]
                df2_intensity = spectrum_ms1.peaks.intensities[df2_idx]
                df2_intensity_list.append(df2_intensity)

                if args.debug_mode:
                    print("df2", df2_idx, spectrum_ms1.peaks.mz[df2_idx], df2_intensity)
                
                if len(df3_idx_arr) != 1:
                    print("WARNING: %d df3 is found!" % len(df3_idx_arr), "df3:", [spectrum_ms1.peaks.mz[x] for x in df3_idx_arr])
                # for df3_idx in df3_idx_arr:
                df3_idx = df3_idx_arr[0]
                df3_intensity = spectrum_ms1.peaks.intensities[df3_idx]
                df3_intensity_list.append(df3_intensity)

                if args.debug_mode:
                    print("df3", df3_idx, spectrum_ms1.peaks.mz[df3_idx], df3_intensity)
            
            else:
                # record RT
                rt_spec_ms1 = spectrum_ms1.metadata["retention_time"]
                rt_list.append(rt_spec_ms1)

                # record scan_number
                scan_num_spec_ms1 = spectrum_ms1.metadata["scan_number"]
                scan_num_list.append(int(scan_num_spec_ms1))

                df1_intensity_list.append(1)
                df2_intensity_list.append(1)
                df3_intensity_list.append(1)
    
    return df_list, rt_list, scan_num_list, df1_intensity_list, df2_intensity_list, df3_intensity_list

def align_peaks_matchms_batch(args, spectrums_ms1_list, spectrums_ms2_list, precMZ_spectID_dict):

    # parser = argparse.ArgumentParser()
    # add_option(parser)
    # args = parser.parse_args()

    # # chech the for the type of parameters
    # assert(isinstance(input_fn, str))
    # assert(isinstance(output_path, str))
    # assert(isinstance(ms2_mass_list, list))
    # assert(isinstance(min_height, float))
    # assert(isinstance(threshold, float))
    # assert(isinstance(charge, int) or isinstance(charge_range, str))
    # assert(polarity in ["positive", "negative"])
    # assert(isinstance(ppm_ms1, float))
    # assert(isinstance(ppm_ms2, float))
    # assert(isinstance(cpd, str))
    # assert(adduct in ["H", "Na", "K", "NH4"])
    # assert(isinstance(addon_mass, str))



    file_name = args.input_fn
    out_path = args.output_path

    if args.debug_mode:
        print("input file name:", file_name)
        print("output path:", out_path)

        print("mass range:", args.min_mass, args.max_mass)
        print("time range:", args.min_rt, args.max_rt)

    z = 3
    # delta_mz = 1e-2
    # df1 = 966.8511
    # df2 = 967.3527
    # df3 = 967.8541

    # df1 = 618.2240
    # df2 = 618.7257
    # df3 = 619.2269

    # df1 = args.ms1_df1
    # df2 = args.ms1_df2
    # df3 = args.ms1_df3

    # assert(df1 is not None)

    # print("df1:", df1, type(df1))
    # print("df2:", df2, type(df2))
    # print("df3:", df3, type(df3))

    # delta_mz1 = 10 * df1 / 1e6
    # delta_mz2 = 10 * df2 / 1e6
    # delta_mz3 = 10 * df3 / 1e6

    # print("delta_mz1:", delta_mz1)
    # print("delta_mz2:", delta_mz2)
    # print("delta_mz3:", delta_mz3)

    sigma = 1.0
    delta=0.2

    min_height = args.min_height
    threshold = args.threshold
    if args.debug_mode:
        print("minimum height:", min_height, "minimum relative height:", threshold)


    # df_ms2_list = [425.1766, 528.1923, 587.2294, 690.2451, 731.2717, 749.2822, 819.2877, 893.3245, 911.3351, 1055.3773, 1114.4144, 1258.4567, 1276.4673]

    # df_ms2_list = [911.3351, 749.2822, 731.2717, 690.2451, 587.2294, 569.2188, 528.1923, 425.1766, 407.1660, 366.1395, 325.1129, 222.0972, 204.0866, 186.0761, 163.0601, 145.0495, 127.0390]

    df_ms2_list = args.ms2_mass_list
    if args.debug_mode:
        print("ms2_mass_list:", df_ms2_list)

    df_cnt_min = args.min_matched_cnt_ms2 # mimimun number of df located in the same spectrum
    if args.debug_mode:
        print("minimum matched count in ms2:", df_cnt_min)

    delta_peak_scan_num = 100 # margin of peak alignment between MS1 and MS2

    # create output path if it doesn't exsit
    creat_path(out_path)

    # # processing the input file
    # if args.debug_mode:
    #     print("starting processing the input file:", file_name)
    # # extract the format of the input file
    # fn_list = file_name.split(".")
    # fn_format = fn_list[-1]
    
    # # processing the MS1 file
    # print("processing MS1...")
    # if fn_format == "mzXML":
    #     spectrums_ms1_all_list = list(load_from_mzxml(file_name, ms_level=1))
    # else:
    #     raise("Unsupported file format.")
    
    # # filter the spectrum_ms1 by rention time range
    # spectrums_ms1_list = []
    # for spectrum_ms1 in spectrums_ms1_all_list:
    #     rt_spec_all_ms1 = spectrum_ms1.metadata["retention_time"]
    #     if rt_spec_all_ms1 >= args.min_rt and rt_spec_all_ms1 <= args.max_rt:
    #         spectrums_ms1_list.append(spectrum_ms1)

    # if args.debug_mode:
    #     print("Number of spectrums in MS1:", len(spectrums_ms1_list))

    # generate cpd list
    cpd_list = args.cpd.split("_")
    cpd_list = [int(cpd_elem) for cpd_elem in cpd_list]
    if len(cpd_list) < 5:
        cpd_list = cpd_list + [0] * (5 - len(cpd_list))
    
    if args.debug_mode:
        print("cpd list:", cpd_list)

    # generate adduct_mass
    # adduct : mass
    adduct_tab = {"H": 1.00728, "Na": 22.98922, "K": 38.96316, "NH4": 18.03383}
    adduct_mass = adduct_tab[args.adduct]

    if args.debug_mode:
        print("adduct, mass:", args.adduct, adduct_mass)

    # create default(list) to hold ms1 searching results
    df_list_dict = defaultdict(list) # z : df_list
    rt_list_dict = defaultdict(list) # z : rt_list
    scan_num_list_dict = defaultdict(list) # z : scan_num_list
    df1_intensity_list_dict = defaultdict(list) # z : df1_intensity_list
    df2_intensity_list_dict = defaultdict(list) # z : df2_intensity_list
    df3_intensity_list_dict = defaultdict(list) # z : df3_intensity_list

    max_intensity = 0
    z_fin = 0

    # search the MS1 file
    if args.charge_range is None:
        charge_list = list(range(1, args.charge + 1))
    else:
        charge_str_list = args.charge_range.split(",")
        charge_list = list(map(int, charge_str_list))
    
    if args.debug_mode:
        print("Charge Range:", charge_list)

    # for z in range(1, args.charge + 1):
    for z in charge_list:
        print("Search MS1 for Charge", z)
        df_list, rt_list, scan_num_list, df1_intensity_list, df2_intensity_list, df3_intensity_list = search_ms1(args, spectrums_ms1_list, z, cpd_list, adduct_mass)

        if df_list[0] < args.min_mass or df_list[0] > args.max_mass:
            continue

        df_list_dict[z] = df_list
        rt_list_dict[z] = rt_list
        scan_num_list_dict[z] = scan_num_list
        df1_intensity_list_dict[z] = df1_intensity_list
        df2_intensity_list_dict[z] = df2_intensity_list
        df3_intensity_list_dict[z] = df3_intensity_list

        # select the z as z_fin with max df1 intensity
        max_df1_intensity = max(df1_intensity_list)

        if args.debug_mode:
            print("z, max_df1_intensity:", z, max_df1_intensity)

        if max_df1_intensity > max_intensity:
            max_intensity = max_df1_intensity
            z_fin = z
    
    if z_fin == 0:
        print("Error: charge should not be 0!")
        exit()

    print("the selected charge:", z_fin)


    # get the selected ms1 info
    df_list = df_list_dict[z_fin]
    df1 = df_list[0]
    df2 = df_list[1]
    df3 = df_list[2]
    print("selected ms1_mass:", df1, df2, df3)

    rt_list = rt_list_dict[z_fin]
    scan_num_list = scan_num_list_dict[z_fin]
    df1_intensity_list = df1_intensity_list_dict[z_fin]
    df2_intensity_list = df2_intensity_list_dict[z_fin]
    df3_intensity_list = df3_intensity_list_dict[z_fin]

    

    
    # # save the debugging df1_ms1 info to csv (only used for debugging)
    # with open(out_path + "/all_df1_rt_mz_ms1.csv", 'w') as f:
    #     writer = csv.writer(f)
    #     rt_df1_list.insert(0, "rt")
    #     df1_mz_all_list.insert(0, "df1")
    #     df2_mz_all_list.insert(0, "df2")
    #     df3_mz_all_list.insert(0, "df3")
    #     writer.writerow(rt_df1_list)
    #     writer.writerow(df1_mz_all_list)
    #     writer.writerow(df2_mz_all_list)
    #     writer.writerow(df3_mz_all_list)


    # find peaks and filter invalid peaks for df1
    df1_peak_idx_ms1_list, df1_intensity_filt_arr, df1_peak_baseline_list = find_filter_peaks(args, rt_list, df1_intensity_list, sigma, min_height, threshold, delta)

    if args.debug_mode:
        print("df1_peak_idx_ms1_list:", df1_peak_idx_ms1_list)
        print("df1_baseline_list:", df1_peak_baseline_list)

    # check the distance of adjacent peaks
    # df1_peak_idx_ms1_list = check_peaks_distance(args, df1_peak_idx_filt_list, rt_list, df1_intensity_filt_arr, delta)
    # print("df1_peak_idx_ms1_list:", df1_peak_idx_ms1_list)

    # df1 Peaks' scan_num list in MS1
    df1_peak_scan_num_ms1_list = [scan_num_list[peak_idx] for peak_idx in df1_peak_idx_ms1_list]

    # draw figures
    if args.flex_mode == "true":
        df1_rt_peak_list = [rt_list[peak_idx] for peak_idx in df1_peak_idx_ms1_list]
        filtered_df1_peak_intensity_list = [df1_intensity_filt_arr[peak_idx] for peak_idx in df1_peak_idx_ms1_list]

        plt.figure()
        plt.plot(rt_list, df1_intensity_filt_arr, color=(0/255,0/255,153/255))
        plt.scatter(df1_rt_peak_list, filtered_df1_peak_intensity_list)
        plt.savefig(out_path + "/rt_intensity_ms1_flex.png")

    else:
        # find peaks and filter invalid peaks for df2
        df2_peak_idx_ms1_list, df2_intensity_filt_arr, df2_peak_baseline_list = find_filter_peaks(args, rt_list, df2_intensity_list, sigma, min_height, threshold, delta)

        # find peaks and filter invalid peaks for df3
        df3_peak_idx_ms1_list, df3_intensity_filt_arr, df3_peak_baseline_list = find_filter_peaks(args, rt_list, df3_intensity_list, sigma, min_height, threshold, delta)

        df1_rt_peak_list = [rt_list[peak_idx] for peak_idx in df1_peak_idx_ms1_list]
        df2_rt_peak_list = [rt_list[peak_idx] for peak_idx in df2_peak_idx_ms1_list]
        df3_rt_peak_list = [rt_list[peak_idx] for peak_idx in df3_peak_idx_ms1_list]
        filtered_df1_peak_intensity_list = [df1_intensity_filt_arr[peak_idx] for peak_idx in df1_peak_idx_ms1_list]
        filtered_df2_peak_intensity_list = [df2_intensity_filt_arr[peak_idx] for peak_idx in df2_peak_idx_ms1_list]
        filtered_df3_peak_intensity_list = [df3_intensity_filt_arr[peak_idx] for peak_idx in df3_peak_idx_ms1_list]

        plt.figure()
        plt.plot(rt_list, df1_intensity_filt_arr, color=(0/255,0/255,153/255))
        plt.plot(rt_list, df2_intensity_filt_arr, color=(102/255,0/255,102/255))
        plt.plot(rt_list, df3_intensity_filt_arr, color=(153/255,76/255,0/255))
        plt.scatter(df1_rt_peak_list, filtered_df1_peak_intensity_list)
        plt.scatter(df2_rt_peak_list, filtered_df2_peak_intensity_list)
        plt.scatter(df3_rt_peak_list, filtered_df3_peak_intensity_list)
        plt.savefig(out_path + "/rt_intensity_ms1.png")

    # # processing the MS2 file
    # print("processing MS2...")
    # if fn_format == "mzXML":
    #     spectrums_ms2_all_list = list(load_from_mzxml(file_name, ms_level=2))
    # else:
    #     raise("Unsupported file format.")
    
    # spectrums_ms2_list = []
    # for spectrum_ms2 in spectrums_ms2_all_list:
    #     rt_spec_all_ms2 = spectrum_ms2.metadata["retention_time"]
    #     if rt_spec_all_ms2 >= args.min_rt and rt_spec_all_ms2 <= args.max_rt:
    #         spectrums_ms2_list.append(spectrum_ms2)

    # if args.debug_mode:
    #     print("Number of spectrums in MS2:", len(spectrums_ms2_list))

    # # iteratively extract spectrum index from spectrums in MS2
    # # list of spectrum_idx of a specific precursor_mz
    # precMZ_spectID_dict = defaultdict(list) # precursor_mz : list of spectrum idx
    # for spec_ms2_idx, spectrum_ms2 in enumerate(spectrums_ms2_list):
    #     spect_precursor_mz = spectrum_ms2.metadata["precursor_mz"]

    #     # precursor_mz_set.add(spectrum_ms2.metadata["precursor_mz"])
    #     precMZ_spectID_dict[spect_precursor_mz].append(spec_ms2_idx)
    
    # if args.debug_mode:
    #     print("# of unique precursor_mz", len(precMZ_spectID_dict))
    # print("precMZ_spectID_dict:", precMZ_spectID_dict)

    # find valid precursor_mz (left_prec_mz < df <= right_prec_mz)
    # precursor_mz_list = find_valid_precursor_mz(df1, precMZ_spectID_dict)
    # precursor_mz_list.append(954.0)

    # find the nearest precursor_mz to df
    precursor_mz_list = find_nearest_precursor_mz(df1, precMZ_spectID_dict)

    if args.debug_mode:
        print("prec_mz_list:", precursor_mz_list)
        print("len(df_ms2_list):", len(df_ms2_list))

    # delta_mz list for all the df
    delta_mz_ms2_list = [10 * df / 1e6 for df in df_ms2_list]

    for precursor_mz in  precursor_mz_list:
        if args.debug_mode:
            print("processing precursor_mz=%f" % precursor_mz)

        spec_ms2_idx_list = precMZ_spectID_dict[precursor_mz]

        if args.debug_mode:
            print("len(spec_ms2_idx_list):",len(spec_ms2_idx_list))

        df_intensity_dict, df_rt_dict, df_scan_num_dict = extract_info_ms2(df_ms2_list, delta_mz_ms2_list, spec_ms2_idx_list, spectrums_ms2_list, df_cnt_min)

        if args.debug_mode:
            print("len(df_intensity_dict)", len(df_intensity_dict))
        
        df_peak_idx_ms2_dict = defaultdict(list) # df : list of peak indices in MS2 
        df_intensity_filt_dict = defaultdict(list) # df : list of filtered intensity
        df_peak_intensity_dict = defaultdict(list) # df : list of peaks' intensity
        df_peak_rt_dict = defaultdict(list) # df : list of peaks' retention time
        df_peak_scan_num_ms2_dict = defaultdict(list) # df : list of peaks's scan number

        for df in df_intensity_dict.keys():
            if args.debug_mode:
                print(df, len(df_intensity_dict[df]))

            # find peaks and filter invalid peaks for df
            df_peak_idx_filt_list, df_intensity_filt_arr, df_peak_baseline_list = find_filter_peaks(args, df_rt_dict[df], df_intensity_dict[df], sigma, delta=delta)

            # dict of list of peak idx for each df in ms2 (df : list of peak idx)
            df_peak_idx_ms2_dict[df] = df_peak_idx_filt_list
            df_intensity_filt_dict[df] = list(df_intensity_filt_arr)

            # dict of list of peak intensity for each df in ms2 (df : list of peak intensity)
            df_peak_intensity_dict[df] = [df_intensity_filt_arr[peak_idx] for peak_idx in df_peak_idx_filt_list]

            # dict of list of peak rt for each df in ms2 (df : list of peak rt)
            df_peak_rt_dict[df] = [df_rt_dict[df][peak_idx] for peak_idx in df_peak_idx_filt_list]

            # dict of list of scan num for each df in ms2 (df : list of peak's scan number)
            df_peak_scan_num_ms2_dict[df] = [df_scan_num_dict[df][peak_idx] for peak_idx in df_peak_idx_filt_list]

            if args.debug_mode:
                print("df_peak_idx_filt_list:", df_peak_idx_filt_list)
        
        # draw figures
        plt.figure()
        for df in df_intensity_dict.keys():
            plt.scatter(df_peak_rt_dict[df], df_peak_intensity_dict[df]) # peak points
            plt.plot(df_rt_dict[df], df_intensity_filt_dict[df]) # all the points of a specific df
        
        plt.savefig(out_path + "/rt_intensity_ms2.png")
    
        # check aligned peaks between MS1 and MS2
        aligned_peak_ms1_2_dict, aligned_peak_tot_intenisty_ms2_dict = find_aligned_peaks(args, df1_peak_idx_ms1_list, df1_peak_scan_num_ms1_list, df_peak_idx_ms2_dict, df_peak_scan_num_ms2_dict, df_peak_intensity_dict, delta_peak_scan_num)

        # print the number of aligned peaks in MS2 for all the peaks in MS1
        for peak_idx_ms1 in df1_peak_idx_ms1_list:
            print("peak_idx_ms1:", peak_idx_ms1, "aligned peak# in MS2:", len(aligned_peak_ms1_2_dict[peak_idx_ms1]))
        

        # # write all the results to CSV file
        # with open(out_path + "/output.csv", 'w') as f:
        #     writer = csv.writer(f)
        #     csv_headline = ["MS1 Peak RT", "MS1 Peak Intensity", "MS1 Peak Height", "MS2 Aligned Peak #"]

        #     writer.writerow(csv_headline)

        #     for i, peak_idx_ms1 in enumerate(df1_peak_idx_ms1_list):
        #         line_list = [rt_list[peak_idx_ms1], df1_intensity_filt_arr[peak_idx_ms1], df1_intensity_filt_arr[peak_idx_ms1] - df1_peak_baseline_list[i], len(aligned_peak_ms1_2_dict[peak_idx_ms1])]

        #         writer.writerow(line_list)
        
        # write results to CSV file
        with open(out_path + "/Glycan_isomers.csv", 'w') as f:
            writer = csv.writer(f)
            csv_head = ["Cpd-AddOnMass", "Note", "RT", "m/z", "charge", "Height(MS1)", "Height(MS2)", "Matched Counts", "Matched(%)"]

            writer.writerow(csv_head)

            for i, peak_idx_ms1 in enumerate(df1_peak_idx_ms1_list):
                line_list = [args.cpd + "-" + args.addon_mass + "(%d)" % i, args.note, rt_list[peak_idx_ms1], df1, z_fin, df1_intensity_filt_arr[peak_idx_ms1], aligned_peak_tot_intenisty_ms2_dict[peak_idx_ms1] ,len(aligned_peak_ms1_2_dict[peak_idx_ms1]), str(len(aligned_peak_ms1_2_dict[peak_idx_ms1])/len(df_ms2_list)*100) + "%"]

                writer.writerow(line_list)


def extrac_dataset_info(input_path, fn="ms_list.csv", dataset_format="mzXML"):
    """extract pre-defined information (i.e. a csv file and dataset list) from input path

    Parameters
    ----------
    input_path : str
        input path of to all the dataset

    Returns
    -------
    dataset_list : list
        The list of dataset under input_path
    cpdAddon_note_list : list
        The list of the (cpd-addOnMass, note)
    note_cpdAddon_list_dict : defaultdict(str)
        The dict of the note-cpdAddon (note : cpd-addOnMass list)
    ms2_mass_dict : defaultdict(str)
        The dict of ms2_mass (cpd : ms2_mass_list). The ms2_mass for the same cpd is divided by space.
    addon_mass_list_dict : defaultdict(str)
        The dict of addon_mass_list (cpd-AddOnMass: add_mass_list)
    """
    dataset_list = []
    cpdAddon_note_list = []
    note_cpdAddon_list_dict = defaultdict(list)
    ms2_mass_dict = defaultdict(str)

    # find all the dataset under input_path
    dirs = os.listdir(input_path)
    # print("dirs", dirs)
    for dir in dirs:
        if dataset_format in dir:
            dataset_list.append(dir.split(".")[0])

    with open(input_path + "/" + fn, "r") as f:
        reader = csv.reader(f)
        for line_idx, row in enumerate(reader):
            # print(row, type(row))
            # remove the header of the csv file
            if line_idx == 0:
                continue
            
            cpd = row[0]
            note = row[1]
            addon_mass = row[2]
            ms2_mass_list = row[3:]

            cpd_addon = cpd + "-" + addon_mass

            # filter the invalied ms2 mass in the ms2_mass_list
            ms2_mass_temp_list = []
            for elem in ms2_mass_list:
                if elem != "N/A" and elem != '':
                    ms2_mass_temp_list.append(elem)
            # merge the ms2_mass_temp_list into a single sequence
            ms2_mass = " ".join(ms2_mass_temp_list)


            cpdAddon_note_list.append((cpd_addon, note))
            note_cpdAddon_list_dict[note].append(cpd_addon)
            ms2_mass_dict[cpd_addon] = ms2_mass

    return dataset_list, cpdAddon_note_list, note_cpdAddon_list_dict, ms2_mass_dict


if __name__ == "__main__":
    set_matchms_logger_level("ERROR")

    # input_path = "./data"

    # output_path = "./result/test"

    # ms2_mass_dict={"5_4_0_1": "425.1766 528.1923 587.2294 690.2451 731.2717 749.2822 819.2877 893.3245 911.3351 1055.3773 1114.4144 1258.4567 1276.4673"}

    # dataset_list = ["20220725_DIA_24mz_stagger_NglycanSTD_dilutex10_DIA_1"]

    # cpdAddon_note_list = [("5_4_0_1", "Sialylated")]

    # input_path = "./data/20230315_batch2"
    # output_path = "./result/20230315_batch2"


    # # input configuration manually
    # input_path = "./data/20230315_batch2_test"
    # output_path = "./result/20230315_batch2_test"
    # ms_list_name = "ms_list.csv"

    # polarity = "positive"
    # charge = 2
    # ppm_ms1 = 10
    # ppm_ms2 = 10

    # adduct = "H"
    # addon_mass = 0
    # threshold = 0.001
    # min_height = 10000
    # match_count_ms2 = 3

    # read configuration from config.ini
    cfg = ConfigParser()
    cfg.read("./config.ini")
    cfg_dict = dict(cfg.items("config"))
    print("cfg_dict", cfg_dict)

    input_path = cfg_dict["input_path"]
    output_path = cfg_dict["output_path"]
    ms_list_name = cfg_dict["ms_list_name"]
    polarity = cfg_dict["polarity"]
    charge = int(cfg_dict["max_charge"])
    ppm_ms1 = float(cfg_dict["ms1_mass_error_ppm"])
    ppm_ms2 = float(cfg_dict["ms2_mass_error_ppm"])
    adduct = cfg_dict["adduct"]
    match_count_ms2 = int(cfg_dict["min_matched_counts"])


    # extract info from ms_list.csv
    dataset_list, cpdAddon_note_list, note_cpdAddon_list_dict, ms2_mass_dict = extrac_dataset_info(input_path, fn=ms_list_name)

    print("dataset_list", dataset_list)
    print("cpdAddon_note_list", cpdAddon_note_list)
    print("note_cpdAddon_list_dict", note_cpdAddon_list_dict)
    print("ms2_mass_dict", ms2_mass_dict)


    comp_root_row_dict = defaultdict(list) # cpd-AddonMass : list of row 
    comp_root_head = ["Cpd-AddonMass", "Note"]
    subtype_root_row_dict = defaultdict(list) # subtype : list of row
    subtype_root_head = ["Subtype"]

    start = timeit.default_timer()

    # extract retention time
    if "min_time_min" in cfg_dict.keys():
        min_rt = float(cfg_dict["min_time_min"])
    else:
        min_rt = 0
    if "max_time_min" in cfg_dict.keys():
        max_rt = float(cfg_dict["max_time_min"])
    else:
        max_rt = float("inf")
    if "debug_mode" in cfg_dict.keys():
        debug_mode = True
    else:
        debug_mode = False

    for dataset in dataset_list:
        # input full path
        input_fn = input_path + "/" + dataset + ".mzXML"
        

        # processing the input file
        if debug_mode:
            print("starting processing the input file:", input_fn)
        # extract the format of the input file
        fn_list = input_fn.split(".")
        fn_format = fn_list[-1]
        
        # processing the MS1 file
        print("processing MS1...")
        if fn_format == "mzXML":
            spectrums_ms1_all_list = list(load_from_mzxml(input_fn, ms_level=1))
        else:
            raise("Unsupported file format.")

        # filter the spectrum_ms1 by rention time range
        spectrums_ms1_list = []
        for spectrum_ms1 in spectrums_ms1_all_list:
            rt_spec_all_ms1 = spectrum_ms1.metadata["retention_time"]
            if rt_spec_all_ms1 >= min_rt and rt_spec_all_ms1 <= max_rt:
                spectrums_ms1_list.append(spectrum_ms1)

        if debug_mode:
            print("Number of spectrums in MS1:", len(spectrums_ms1_list))
        
        
        # processing the MS2 file
        print("processing MS2...")
        if fn_format == "mzXML":
            spectrums_ms2_all_list = list(load_from_mzxml(input_fn, ms_level=2))
        else:
            raise("Unsupported file format.")

        spectrums_ms2_list = []
        for spectrum_ms2 in spectrums_ms2_all_list:
            rt_spec_all_ms2 = spectrum_ms2.metadata["retention_time"]
            if rt_spec_all_ms2 >= min_rt and rt_spec_all_ms2 <= max_rt:
                spectrums_ms2_list.append(spectrum_ms2)

        if debug_mode:
            print("Number of spectrums in MS2:", len(spectrums_ms2_list))
        
        # iteratively extract spectrum index from spectrums in MS2
        # list of spectrum_idx of a specific precursor_mz
        precMZ_spectID_dict = defaultdict(list) # precursor_mz : list of spectrum idx
        for spec_ms2_idx, spectrum_ms2 in enumerate(spectrums_ms2_list):
            spect_precursor_mz = spectrum_ms2.metadata["precursor_mz"]

            # precursor_mz_set.add(spectrum_ms2.metadata["precursor_mz"])
            precMZ_spectID_dict[spect_precursor_mz].append(spec_ms2_idx)
        
        if debug_mode:
            print("# of unique precursor_mz", len(precMZ_spectID_dict))
        

        comp_root_head += ["Height (MS1)_Data File " + dataset, "Relative Height (MS1 %)_Data File" + dataset, "Height (MS2)_Data File " + dataset, "Relative Height (MS2 %)_Data File" + dataset]
        subtype_root_head += ["Height (MS1)_Data File " + dataset, "Relative Height (MS1 %)_Data File" + dataset, "Height (MS2)_Data File " + dataset, "Relative Height (MS2 %)_Data File" + dataset]
        # aggregated csv information
        isomers_row_list = []
        # cpd-AddonMass : total height_ms1
        cpd_tot_h_ms1_dict = defaultdict(float)
        # cpd-AddonMass : total height_ms2
        cpd_tot_h_ms2_dict = defaultdict(float)
        # cpd-AddonMass : list of max match%
        cpd_match_perc_list_dict = defaultdict(list)

        # note : total height_ms1
        note_tot_h_ms1_dict = defaultdict(float)
        # note : total height_ms2
        note_tot_h_ms2_dict = defaultdict(float)

        for cpd_addon, note in cpdAddon_note_list:

            cpd = cpd_addon.split("-")[0]
            addon_mass = cpd_addon.split("-")[1]

            print("cpd_addon", cpd_addon)

            # create output paths if not existed
            output_fd = output_path + "/" + dataset + "/" + cpd_addon
            creat_path(output_fd)

            # # create configuration list
            # config_list = ["--input_fn", input_fn, "--output_path", output_fd, " --ms2_mass_list", ms2_mass_dict[cpd_addon], "--polarity", polarity, "--charge", str(charge),"--ppm_ms1", str(ppm_ms1), "--ppm_ms2", str(ppm_ms2), "--cpd", cpd, "--adduct", adduct, "--addon_mass", addon_mass, "--min_matched_cnt_ms2", str(match_count_ms2), "--note", note]

            # if "min_rel_height" in cfg_dict.keys():
            #     config_list += ["--threshold", cfg_dict["min_rel_height"]]
            # if "min_height" in cfg_dict.keys():
            #     config_list += ["--min_height", cfg_dict["min_height"]]

            # if "min_mass" in cfg_dict.keys():
            #     config_list += ["--min_mass", cfg_dict["min_mass"]]
            # if "max_mass" in cfg_dict.keys():
            #     config_list += ["--max_mass", cfg_dict["max_mass"]]
            # if "min_time_min" in cfg_dict.keys():
            #     config_list += ["--min_rt", cfg_dict["min_time_min"]]
            # if "max_time_min" in cfg_dict.keys():
            #     config_list += ["--max_rt", cfg_dict["max_time_min"]]
            # if "flex_mode" in cfg_dict.keys():
            #     config_list += ["--flex_mode", cfg_dict["flex_mode"]]
            # if "charge_range" in cfg_dict.keys():
            #     config_list += ["--charge_range", cfg_dict["charge_range"]]
            # if "debug_mode" in cfg_dict.keys():
            #     config_list += ["--debug_mode"]

            # config = " ".join(config_list)
            # print("config:", config)


            # create configuration list for an Arguments class
            if "min_rel_height" in cfg_dict.keys():
                threshold = cfg_dict["min_rel_height"]
            else:
                threshold = 0.001
            if "min_height" in cfg_dict.keys():
                min_height = cfg_dict["min_height"]
            else:
                min_height = 5000
            if "min_mass" in cfg_dict.keys():
                min_mass = cfg_dict["min_mass"]
            else:
                min_mass = 0
            if "max_mass" in cfg_dict.keys():
                max_mass = cfg_dict["max_mass"]
            else:
                max_mass = float("inf")
            # if "min_time_min" in cfg_dict.keys():
            #     min_rt = cfg_dict["min_time_min"]
            # else:
            #     min_rt = 0
            # if "max_time_min" in cfg_dict.keys():
            #     max_rt = cfg_dict["max_time_min"]
            # else:
            #     max_rt = float("inf")
            if "flex_mode" in cfg_dict.keys():
                flex_mode = cfg_dict["flex_mode"]
            else:
                flex_mode = "false"
            if "charge_range" in cfg_dict.keys():
                charge_range = cfg_dict["charge_range"]
            else:
                charge_range = None
            # if "debug_mode" in cfg_dict.keys():
            #     debug_mode = True
            # else:
            #     debug_mode = False
            if "max_aligned_record_ms2" in cfg_dict.keys():
                max_aligned_record_ms2 = int(cfg_dict["max_aligned_record_ms2"])
            else:
                max_aligned_record_ms2 = float("inf")
            
            args = Arguments(input_fn, output_fd, ms2_mass_dict[cpd_addon], min_height, threshold, charge, charge_range, polarity, ppm_ms1, ppm_ms2, cpd, adduct, addon_mass, match_count_ms2, note, min_mass, max_mass, min_rt, max_rt, flex_mode, debug_mode, max_aligned_record_ms2)


            # os.system("python3 align_peaks_matchms_batch.py " + config + " | tee " + output_fd + "/debug.log")
            align_peaks_matchms_batch(args, spectrums_ms1_list, spectrums_ms2_list, precMZ_spectID_dict)
            plt.close('all')

            # read Glycan_isomers.csv for each cpd_addon
            with open(output_fd + "/Glycan_isomers.csv", "r") as f:
                reader = csv.reader(f)
                # match_percent_max = 0
                # match_percent_tot = 0
                # peak_num = 0
                for line_idx, row in enumerate(reader):
                    # print(row, type(row))
                    # remove the header of the csv file
                    if line_idx == 0:
                        continue
                    
                    #isomers
                    isomers_row_list.append(row)

                    # composition
                    cpd_tot_h_ms1_dict[cpd_addon] += float(row[5])
                    cpd_tot_h_ms2_dict[cpd_addon] += float(row[6])
                    cpd_match_perc_list_dict[cpd_addon].append(float(row[8].split("%")[0]))

                    # subtype
                    note_tot_h_ms1_dict[note] += float(row[5])
                    note_tot_h_ms2_dict[note] += float(row[6])
        
        # delete ms data
        # print("freeing MS data...")
        del spectrums_ms1_all_list
        del spectrums_ms1_list
        del spectrums_ms2_all_list
        del spectrums_ms2_list
        del precMZ_spectID_dict
        gc.collect()

        # generate isomers form
        with open(output_path + "/" + dataset + "/Glycan_isomers.csv", "w") as f:
            writer = csv.writer(f)
            # write headline
            csv_head = ["Cpd-AddOnMass", "Note", "RT", "m/z", "charge", "Height(MS1)", "Height(MS2)", "Math Counts", "Matched(%)"]
            writer.writerow(csv_head)
            # print(isomers_row_list)

            for row in isomers_row_list:
                writer.writerow(row)

        # generate composition form

        # calculate total height for all cpd of all AddOnMass in ms1 and ms2
        tot_h_ms1 = 0
        tot_h_ms2 = 0
        for cpd_addon, note in cpdAddon_note_list:
            tot_h_ms1 += cpd_tot_h_ms1_dict[cpd_addon]
            tot_h_ms2 += cpd_tot_h_ms2_dict[cpd_addon]
        
        with open(output_path + "/" + dataset + "/Glycan_composition.csv", "w") as f:
            writer = csv.writer(f)
            # write headline
            csv_head = ["Cpd-AddonMass", "Note", "Height (MS1)", "Relative Height (MS1 %)", "Height (MS2)", "Relative Height (MS2 %)", "Max Matched (%)", "Avg Matched (%)"]
            writer.writerow(csv_head)

            for cpd_addon, note in cpdAddon_note_list:

                # print(dataset, cpd_addon)
                match_percent_max = 0
                match_percent_avg = 0
                if cpd_match_perc_list_dict[cpd_addon]:
                    match_percent_max = max(cpd_match_perc_list_dict[cpd_addon])
                    match_percent_avg = mean(cpd_match_perc_list_dict[cpd_addon])

                writer.writerow([cpd_addon, note, cpd_tot_h_ms1_dict[cpd_addon], cpd_tot_h_ms1_dict[cpd_addon] / tot_h_ms1 * 100, cpd_tot_h_ms2_dict[cpd_addon], cpd_tot_h_ms2_dict[cpd_addon] / tot_h_ms2 * 100, match_percent_max, match_percent_avg])

                comp_root_row_dict[cpd_addon] += [cpd_tot_h_ms1_dict[cpd_addon], cpd_tot_h_ms1_dict[cpd_addon] / tot_h_ms1 * 100, cpd_tot_h_ms2_dict[cpd_addon], cpd_tot_h_ms2_dict[cpd_addon] / tot_h_ms2 * 100]
        
        # subtype
        with open(output_path + "/" + dataset + "/Glycan_subtype.csv", "w") as f:
            writer = csv.writer(f)
            # write headline
            csv_head = ["subtype", "Height (MS1)", "Relative Height (MS1 %)", "Height (MS2)", "Relative Height (MS2 %)"]
            writer.writerow(csv_head)

            for note in note_tot_h_ms1_dict.keys():
                writer.writerow([note, note_tot_h_ms1_dict[note], note_tot_h_ms1_dict[note] / tot_h_ms1 * 100, note_tot_h_ms2_dict[note], note_tot_h_ms2_dict[note]/ tot_h_ms2 * 100])

                subtype_root_row_dict[note] += [note_tot_h_ms1_dict[note], note_tot_h_ms1_dict[note] / tot_h_ms1 * 100, note_tot_h_ms2_dict[note], note_tot_h_ms2_dict[note]/ tot_h_ms2 * 100]

    
    # Root Glycan_composition form
    with open(output_path + "/Glycan_composition_combined.csv", "w") as f:
        writer = csv.writer(f)
        writer.writerow(comp_root_head)

        for cpd_addon, note in cpdAddon_note_list:
            row = [cpd_addon, note] + comp_root_row_dict[cpd_addon]
            writer.writerow(row)
    
    # Root Glycan_subtype form
    with open(output_path + "/Glycan_subtype_combined.csv", "w") as f:
        writer = csv.writer(f)
        writer.writerow(subtype_root_head)

        for note in subtype_root_row_dict.keys():
            row = [note] + subtype_root_row_dict[note]
            writer.writerow(row)
    
    end = timeit.default_timer()
    print("Running time: %s Seconds"%(end-start))











