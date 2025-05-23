r"""
Convert SDRF files for use with OpenMS.

Example command:

parse_sdrf convert-openms \
    -s .\sdrf-pipelines\sdrf_pipelines\large_sdrf.tsv \
    -c '[characteristics[biological replicate],characteristics[individual]]'
"""

import logging
import re
from collections import Counter

import pandas as pd

from sdrf_pipelines.openms.unimod import UnimodDatabase
from sdrf_pipelines.utils.utils import tsv_line

logger = logging.getLogger(__name__)


class FileToColumnEntries:
    file2mods: dict[str, tuple] = {}
    file2pctol: dict[str, str] = {}
    file2pctolunit: dict[str, str] = {}
    file2fragtol: dict[str, str] = {}
    file2fragtolunit: dict[str, str] = {}
    file2diss: dict[str, str] = {}
    file2enzyme: dict[str, str] = {}
    file2source: dict[str, str] = {}
    file2label: dict[str, list[str]] = {}
    file2fraction: dict[str, str] = {}
    file2combined_factors: dict[str, str] = {}
    file2technical_rep: dict[str, str] = {}


def infer_tmtplex(label_set: set) -> str:
    """Infer the tmt plex from a set of labels"""
    if len(label_set) > 16 or "TMT134C" in label_set or "TMT135N" in label_set:
        label = "tmt18plex"
    elif (
        len(label_set) > 11
        or "TMT134N" in label_set
        or "TMT133C" in label_set
        or "TMT133N" in label_set
        or "TMT132C" in label_set
        or "TMT132N" in label_set
    ):
        label = "tmt16plex"
    elif len(label_set) == 11 or "TMT131C" in label_set:
        label = "tmt11plex"
    elif len(label_set) > 6:
        label = "tmt10plex"
    else:
        label = "tmt6plex"
    return label


def get_openms_file_name(raw, extension_convert: str | None = None):
    """
    Convert file name for OpenMS. If extension_convert is set, the extension will be converted to the specified format.
    - file.raw -> file.mzML  (extension_convert=raw:mzML)
    - file.mzML -> file.mzML  (extension_convert=mzML:mzML)
    - file.mzML -> file.mzml  (extension_convert=mzML:mzml)
    - file.mzml -> file.mzML  (extension_convert=mzml:mzML)
    - file.d -> file.mzML  (extension_convert=d:mzML)
    - file.d -> file.d  (extension_convert=d:d)
    :param raw: raw file name
    :param extension_convert: convert extension to specified format
    :return: converted file name
    """

    if extension_convert is None:
        return raw

    possible_extension = ["raw", "mzML", "mzml", "d"]
    extension_convert_list = extension_convert.split(",")
    extension_convert_dict = {}
    for extension_convert in extension_convert_list:
        current_extension, new_extension = extension_convert.split(":")
        extension_convert_dict[current_extension] = new_extension

    raw_bkp = raw
    for current_extension, target_extension in extension_convert_dict.items():
        if raw.lower().endswith(current_extension.lower()):
            raw = raw.removesuffix(current_extension)
            raw += target_extension
            if not any(raw.endswith(x) for x in possible_extension):
                raise RuntimeError(
                    f"Error converting extension, {raw_bkp} -> {raw},"
                    " the ending file does not have any of the supported"
                    f" extensions {possible_extension}"
                )
            return raw

    return raw


def parse_tolerance(pc_tol_str: str, units=("ppm", "da")) -> tuple[str | None, str | None]:
    """Find tolerance in string."""
    # check that only one unit is specified?
    pc_tol_str = pc_tol_str.lower()
    for unit in units:
        if unit in pc_tol_str:
            tol = pc_tol_str.split(unit)[0].strip()
            if f" {unit}" not in pc_tol_str:
                msg = f"Missing whitespace in precursor mass tolerance: {pc_tol_str} Adding it: {tol} {unit}"
                logger.warning(msg)
            _ = float(tol)  # should be an number
            if unit == "da":
                unit = unit.capitalize()
            return tol, unit
    return None, None


TMT_PLEXES = {
    "tmt18plex": {
        "TMT126": 1,
        "TMT127N": 2,
        "TMT127C": 3,
        "TMT128N": 4,
        "TMT128C": 5,
        "TMT129N": 6,
        "TMT129C": 7,
        "TMT130N": 8,
        "TMT130C": 9,
        "TMT131N": 10,
        "TMT131C": 11,
        "TMT132N": 12,
        "TMT132C": 13,
        "TMT133N": 14,
        "TMT133C": 15,
        "TMT134N": 16,
        "TMT134C": 17,
        "TMT135N": 18,
    },
    "tmt16plex": {
        "TMT126": 1,
        "TMT127N": 2,
        "TMT127C": 3,
        "TMT128N": 4,
        "TMT128C": 5,
        "TMT129N": 6,
        "TMT129C": 7,
        "TMT130N": 8,
        "TMT130C": 9,
        "TMT131N": 10,
        "TMT131C": 11,
        "TMT132N": 12,
        "TMT132C": 13,
        "TMT133N": 14,
        "TMT133C": 15,
        "TMT134N": 16,
    },
    "tmt11plex": {
        "TMT126": 1,
        "TMT127N": 2,
        "TMT127C": 3,
        "TMT128N": 4,
        "TMT128C": 5,
        "TMT129N": 6,
        "TMT129C": 7,
        "TMT130N": 8,
        "TMT130C": 9,
        "TMT131N": 10,
        "TMT131C": 11,
    },
    "tmt10plex": {
        "TMT126": 1,
        "TMT127N": 2,
        "TMT127C": 3,
        "TMT128N": 4,
        "TMT128C": 5,
        "TMT129N": 6,
        "TMT129C": 7,
        "TMT130N": 8,
        "TMT130C": 9,
        "TMT131": 10,
    },
    "tmt6plex": {
        "TMT126": 1,
        "TMT127": 2,
        "TMT128": 3,
        "TMT129": 4,
        "TMT130": 5,
        "TMT131": 6,
    },
}


class OpenMS:
    def __init__(self) -> None:
        super().__init__()
        self.warnings: dict[str, int] = {}
        self._unimod_database = UnimodDatabase()
        # Hardcode enzymes from OpenMS
        self.enzymes = {
            "Glutamyl endopeptidase": "glutamyl endopeptidase",
            "Trypsin/p": "Trypsin/P",
            "Trypchymo": "TrypChymo",
            "Lys-c": "Lys-C",
            "Lys-c/p": "Lys-C/P",
            "Lys-n": "Lys-N",
            "Arg-c": "Arg-C",
            "Arg-c/p": "Arg-C/P",
            "Asp-n": "Asp-N",
            "Asp-n/b": "Asp-N/B",
            "Asp-n_ambic": "Asp-N_ambic",
            "Chymotrypsin/p": "Chymotrypsin/P",
            "Cnbr": "CNBr",
            "V8-de": "V8-DE",
            "V8-e": "V8-E",
            "Elastase-trypsin-chymotrypsin": "elastase-trypsin-chymotrypsin",
            "Pepsina": "PepsinA",
            "Unspecific cleavage": "unspecific cleavage",
            "No cleavage": "no cleavage",
        }

        # for itraq label
        self.itraq4plex = {"itraq114": 1, "itraq115": 2, "itraq116": 3, "itraq117": 4}
        self.itraq8plex = {
            "itraq113": 1,
            "itraq114": 2,
            "itraq115": 3,
            "itraq116": 4,
            "itraq117": 5,
            "itraq118": 6,
            "itraq119": 7,
            "itraq121": 8,
        }

        #  for light, medium and heavy. E.g. Label:13C(2)15N(2) (K) as light or Dimethyl:2H(2)13C (K) as light
        self.silac3 = {"silac light": 1, "silac medium": 2, "silac heavy": 3}
        self.silac2 = {"silac light": 1, "silac heavy": 2}

    # convert modifications in sdrf file to OpenMS notation
    def openms_ify_mods(self, sdrf_mods):
        oms_mods = []

        for m in sdrf_mods:
            if "AC=UNIMOD" not in m and "AC=Unimod" not in m:
                raise ValueError("only UNIMOD modifications supported. " + m)

            name = re.search("NT=(.+?)(;|$)", m).group(1)
            name = name.capitalize()

            accession = re.search("AC=(.+?)(;|$)", m).group(1)
            ptm = self._unimod_database.get_by_accession(accession)
            if ptm is not None:
                name = ptm.get_name()

            # workaround for missing PP in some sdrf TODO: fix in sdrf spec?
            if re.search("PP=(.+?)(;|$)", m) is None:
                pp = "Anywhere"
            else:
                pp = re.search("PP=(.+?)(;|$)", m).group(
                    1
                )  # one of [Anywhere, Protein N-term, Protein C-term, Any N-term, Any C-term

            ta = ""
            if re.search("TA=(.+?)(;|$)", m) is None:  # TODO: missing in sdrf.
                warning_message = "Warning no TA= specified. Setting to N-term or C-term if possible."
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                if "C-term" in pp:
                    ta = "C-term"
                elif "N-term" in pp:
                    ta = "N-term"
                else:
                    warning_message = "Reassignment not possible. Skipping."
                    # print(warning_message + " "+ m)
                    self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
            else:
                ta = re.search("TA=(.+?)(;|$)", m).group(1)  # target amino-acid
            aa = ta.split(",")  # multiply target site e.g., S,T,Y including potentially termini "C-term"

            if pp in ("Protein N-term", "Protein C-term"):
                for a in aa:
                    if a in ("C-term", "N-term"):  # no site specificity
                        oms_mods.append(name + " (" + pp + ")")  # any Protein N/C-term
                    else:
                        oms_mods.append(name + " (" + pp + " " + a + ")")  # specific Protein N/C-term
            elif pp in ("Any N-term", "Any C-term"):
                pp = pp.replace("Any ", "")  # in OpenMS we just use N-term and C-term
                for a in aa:
                    if a in ("C-term", "N-term"):  # no site specificity
                        oms_mods.append(name + " (" + pp + ")")  # any N/C-term
                    else:
                        oms_mods.append(name + " (" + pp + " " + a + ")")  # specific N/C-term
            else:  # Anywhere in the peptide
                for a in aa:
                    oms_mods.append(name + " (" + a + ")")  # specific site in peptide

        return ",".join(oms_mods)

    def openms_convert(
        self,
        sdrf_file: str,
        one_table: bool = False,
        legacy: bool = False,
        verbose: bool = False,
        split_by_columns: str | None = None,
        extension_convert: str | None = None,
    ):
        print("PROCESSING: " + sdrf_file + '"')

        # convert list passed on command line '[assay name,comment[fraction identifier]]' to python list
        if split_by_columns:
            split_by_columns = split_by_columns[1:-1]  # trim '[' and ']'
            split_by_columns_list = split_by_columns.split(",")
            print("User selected factor columns: " + str(split_by_columns_list))

        # load sdrf file
        sdrf = pd.read_table(sdrf_file)
        null_cols = sdrf.columns[sdrf.isnull().any()]
        if sdrf.isnull().values.any():
            raise ValueError(
                "Encountered empty cells while reading SDRF."
                "Please check your file, e.g. for too many column headers or empty fields"
                f"Columns with empty values: {list(null_cols)}"
            )
        sdrf = sdrf.astype(str)
        sdrf.columns = sdrf.columns.str.lower()  # convert column names to lower-case

        # map filename to tuple of [fixed, variable] mods
        mod_cols = [
            c for c in sdrf.columns if c.startswith("comment[modification parameters")
        ]  # columns with modification parameters

        if split_by_columns:
            factor_cols = split_by_columns_list  # enforce columns as factors if names provided by user
        else:
            factor_cols = [c for c in sdrf.columns if c.startswith("factor value[") and len(sdrf[c].unique()) >= 1]

            characteristics_cols = [
                c for c in sdrf.columns if c.startswith("characteristics[") and len(sdrf[c].unique()) >= 1
            ]
            # and remove characteristics columns already present as factor
            characteristics_cols = self.removeRedundantCharacteristics(characteristics_cols, sdrf, factor_cols)
            print("Factor columns: " + str(factor_cols))
            print("Characteristics columns (those covered by factor columns removed): " + str(characteristics_cols))

        source_name_list: list[str] = []
        source_name2n_reps: dict[str, int] = {}

        f2c = FileToColumnEntries()
        for row_index, row in sdrf.iterrows():
            # extract mods
            all_mods = list(row[mod_cols])
            # print(all_mods)
            var_mods = [
                m for m in all_mods if "MT=variable" in m or "MT=Variable" in m
            ]  # workaround for capitalization
            var_mods.sort()
            fixed_mods = [m for m in all_mods if "MT=fixed" in m or "MT=Fixed" in m]  # workaround for capitalization
            fixed_mods.sort()
            if verbose:
                print(row)
            raw = row["comment[data file]"]
            fixed_mods_string = ""
            if fixed_mods is not None:
                fixed_mods_string = self.openms_ify_mods(fixed_mods)

            variable_mods_string = ""
            if var_mods is not None:
                variable_mods_string = self.openms_ify_mods(var_mods)

            f2c.file2mods[raw] = (fixed_mods_string, variable_mods_string)

            source_name = row["source name"]
            f2c.file2source[raw] = source_name
            if source_name not in source_name_list:
                source_name_list.append(source_name)

            if "comment[precursor mass tolerance]" in row:
                pc_tol_str = row["comment[precursor mass tolerance]"].strip()

                tol, unit = parse_tolerance(pc_tol_str)
                if tol is None or unit is None:
                    raise ValueError(f"Cannot read precursor mass tolerance: {pc_tol_str}")
                    # warning_message = "Invalid precursor mass tolerance set. Assuming 10 ppm."
                    # self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                    # f2c.file2pctol[raw] = "10"
                    # f2c.file2pctolunit[raw] = "ppm"
                f2c.file2pctol[raw] = tol
                f2c.file2pctolunit[raw] = unit
            else:
                warning_message = "No precursor mass tolerance set. Assuming 10 ppm."
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                f2c.file2pctol[raw] = "10"
                f2c.file2pctolunit[raw] = "ppm"

            if "comment[fragment mass tolerance]" in row:
                f_tol_str = row["comment[fragment mass tolerance]"].strip()
                tol, unit = parse_tolerance(f_tol_str)
                if tol is None or unit is None:
                    raise ValueError(f"Cannot read precursor mass tolerance: {f_tol_str}")
                    # warning_message = "Invalid fragment mass tolerance set. Assuming 20 ppm."
                    # self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                    # f2c.file2fragtol[raw] = "20"
                    # f2c.file2fragtolunit[raw] = "ppm"
                f2c.file2fragtol[raw] = tol
                f2c.file2fragtolunit[raw] = unit
            else:
                warning_message = "No fragment mass tolerance set. Assuming 20 ppm."
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                f2c.file2fragtol[raw] = "20"
                f2c.file2fragtolunit[raw] = "ppm"

            if "comment[dissociation method]" in row:
                search_result_diss_method = re.search("NT=(.+?)(;|$)", row["comment[dissociation method]"])
                if search_result_diss_method is not None:
                    diss_method = search_result_diss_method.group(1)
                    f2c.file2diss[raw] = diss_method.upper()
                else:
                    warning_message = "No dissociation method provided. Assuming HCD."
                    self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                    f2c.file2diss[raw] = "HCD"
            else:
                warning_message = "No dissociation method provided. Assuming HCD."
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                f2c.file2diss[raw] = "HCD"

            if "comment[technical replicate]" in row:
                technical_replicate = str(row["comment[technical replicate]"])
                if "not available" in technical_replicate:
                    f2c.file2technical_rep[raw] = "1"
                else:
                    f2c.file2technical_rep[raw] = technical_replicate
            else:
                f2c.file2technical_rep[raw] = "1"

            # store highest replicate number for this source name
            if source_name in source_name2n_reps:
                source_name2n_reps[source_name] = max(
                    int(source_name2n_reps[source_name]),
                    int(f2c.file2technical_rep[raw]),
                )
            else:
                source_name2n_reps[source_name] = int(f2c.file2technical_rep[raw])
            cleavage_agent_details = row["comment[cleavage agent details]"]
            enzyme_search_result = re.search("NT=(.+?)(;|$)", cleavage_agent_details)

            if enzyme_search_result is not None:
                enzyme = enzyme_search_result.group(1)
            else:
                logger.warning(
                    f"Could not find a cleavage agent in {cleavage_agent_details} with the regex {r'NT=(.+?)(;|$)'}."
                )
                enzyme = ""

            enzyme = enzyme.capitalize()
            # This is to check if the openMS map of enzymes
            if enzyme in self.enzymes:
                enzyme = self.enzymes[enzyme]

            f2c.file2enzyme[raw] = enzyme

            if "comment[fraction identifier]" in row:
                fraction = str(row["comment[fraction identifier]"])
                if "not available" in fraction:
                    f2c.file2fraction[raw] = "1"
                else:
                    f2c.file2fraction[raw] = fraction
            else:
                f2c.file2fraction[raw] = "1"

            search_result_label = re.search("NT=(.+?)(;|$)", row["comment[label]"])
            if search_result_label is not None:
                label = search_result_label.group(1)
                f2c.file2label[raw] = [label]
            else:
                if "TMT" in row["comment[label]"]:
                    label = sdrf[sdrf["comment[data file]"] == raw]["comment[label]"].tolist()
                elif "SILAC" in row["comment[label]"]:
                    label = sdrf[sdrf["comment[data file]"] == raw]["comment[label]"].tolist()
                elif "label free sample" in row["comment[label]"]:
                    label = ["label free sample"]
                elif "ITRAQ" in row["comment[label]"]:
                    label = sdrf[sdrf["comment[data file]"] == raw]["comment[label]"].tolist()
                else:
                    raise ValueError("Label " + str(row["comment[label]"]) + " is not recognized")
                f2c.file2label[raw] = label

            if not split_by_columns:
                # extract factors (or characteristics if factors are missing), and generate one condition for
                # every combination of factor values present in the data
                combined_factors = self.combine_factors_to_conditions(characteristics_cols, factor_cols, row)
            else:
                # take only entries of splitting columns to generate the conditions
                combined_factors = "|".join(list(row[split_by_columns_list]))

            # add condition from factors as extra column to sdrf so we can easily filter in pandas
            sdrf["_conditions_from_factors"] = pd.Series([None] * sdrf.shape[0], dtype="object")
            sdrf.at[row_index, "_conditions_from_factors"] = combined_factors

            f2c.file2combined_factors[raw + row["comment[label]"]] = combined_factors

            # print("Combined factors: " + str(combined_factors))

        conditions = Counter(f2c.file2combined_factors.values()).keys()
        files_per_condition = Counter(f2c.file2combined_factors.values()).values()
        print("Conditions (" + str(len(conditions)) + "): " + str(conditions))
        print("Files per condition: " + str(files_per_condition))

        if not split_by_columns:
            # output of search settings for every row in sdrf
            self.save_search_settings_to_file("openms.tsv", sdrf, f2c)

            # output one experimental design file
            if one_table:
                self.writeOneTableExperimentalDesign(
                    "experimental_design.tsv",
                    legacy,
                    sdrf,
                    f2c.file2technical_rep,
                    source_name_list,
                    source_name2n_reps,
                    f2c.file2combined_factors,
                    f2c.file2label,
                    extension_convert,
                    f2c.file2fraction,
                )
            else:  # two table format
                self.writeTwoTableExperimentalDesign(
                    "experimental_design.tsv",
                    sdrf,
                    f2c.file2technical_rep,
                    source_name_list,
                    source_name2n_reps,
                    f2c.file2label,
                    extension_convert,
                    f2c.file2fraction,
                    f2c.file2combined_factors,
                )

        else:  # split by columns
            for index, c in enumerate(conditions):
                # extract rows from sdrf for current condition
                split_sdrf = sdrf.loc[sdrf["_conditions_from_factors"] == c]
                output_filename = "openms.tsv." + str(index)
                self.save_search_settings_to_file(output_filename, split_sdrf, f2c)

                # output of experimental design
                output_filename = "experimental_design.tsv." + str(index)
                if one_table:
                    self.writeOneTableExperimentalDesign(
                        output_filename,
                        legacy,
                        split_sdrf,
                        f2c.file2technical_rep,
                        source_name_list,
                        source_name2n_reps,
                        f2c.file2combined_factors,
                        f2c.file2label,
                        extension_convert,
                        f2c.file2fraction,
                    )
                else:  # two table format
                    self.writeTwoTableExperimentalDesign(
                        output_filename,
                        split_sdrf,
                        f2c.file2technical_rep,
                        source_name_list,
                        source_name2n_reps,
                        f2c.file2label,
                        extension_convert,
                        f2c.file2fraction,
                        f2c.file2combined_factors,
                    )

        self.reportWarnings(sdrf_file)

    def combine_factors_to_conditions(self, characteristics_cols, factor_cols, row):
        all_factors = list(row[factor_cols])
        combined_factors = "|".join(all_factors)
        if combined_factors == "":
            # fallback to characteristics (use them as factors)
            all_factors = list(row[characteristics_cols])
            combined_factors = "|".join(all_factors)
            if combined_factors == "":
                warning_message = "No factors specified. Adding dummy factor used as condition."
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                combined_factors = None
            else:
                warning_message = (
                    "No factors specified. Adding non-redundant characteristics as factor. Will be used "
                    "as condition. "
                )
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
        return combined_factors

    def removeRedundantCharacteristics(self, characteristics_cols, sdrf, factor_cols):
        redundant_characteristics_cols = set()
        for c in characteristics_cols:
            c_col = sdrf[c]  # select characteristics column
            for f in factor_cols:  # Iterate over all factor columns
                f_col = sdrf[f]  # select factor column
                if c_col.equals(f_col):
                    redundant_characteristics_cols.add(c)
        characteristics_cols = [x for x in characteristics_cols if x not in redundant_characteristics_cols]
        return characteristics_cols

    def reportWarnings(self, sdrf_file):
        if len(self.warnings) != 0:
            for k, v in self.warnings.items():
                print('WARNING: "' + k + '" occurred ' + str(v) + " times.")
        print("SUCCESS (WARNINGS=" + str(len(self.warnings)) + "): " + sdrf_file)

    def writeTwoTableExperimentalDesign(
        self,
        output_filename,
        sdrf,
        file2technical_rep,
        source_name_list,
        source_name2n_reps,
        file2label,
        extension_convert,
        file2fraction,
        file2combined_factors,
    ):
        openms_file_header = [
            "Fraction_Group",
            "Fraction",
            "Spectra_Filepath",
            "Label",
            "Sample",
        ]
        f = ""
        f += "\t".join(openms_file_header) + "\n"
        label_index = dict(zip(sdrf["comment[data file]"], [0] * len(sdrf["comment[data file]"])))
        sample_identifier_re = re.compile(r"sample (\d+)$", re.IGNORECASE)
        Fraction_group = {}
        sample_id_map = {}
        sample_id = 1
        pre_frac_group = 1
        raw_frac = {}
        for _, row in sdrf.iterrows():
            raw = row["comment[data file]"]
            source_name = row["source name"]
            replicate = file2technical_rep[raw]

            # calculate fraction group by counting all technical replicates of the preceeding source names
            source_name_index = source_name_list.index(source_name)
            offset = 0
            for i in range(source_name_index):
                offset = offset + int(source_name2n_reps[source_name_list[i]])

            fraction_group = offset + int(replicate)

            if fraction_group not in raw_frac:
                raw_frac[fraction_group] = [raw]

                if raw in Fraction_group:
                    if fraction_group < Fraction_group[raw]:
                        Fraction_group[raw] = fraction_group
                else:
                    Fraction_group[raw] = fraction_group

                # make fraction group consecutive
                if Fraction_group[raw] > pre_frac_group + 1:
                    Fraction_group[raw] = pre_frac_group + 1
                pre_frac_group = Fraction_group[raw]

            else:
                raw_frac[fraction_group].append(raw)
                Fraction_group[raw] = Fraction_group[raw_frac[fraction_group][0]]

            if re.search(sample_identifier_re, source_name) is not None:
                sample = re.search(sample_identifier_re, source_name).group(1)
            else:
                warning_message = "No sample identifier"
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1

                # Solve non-sample id expression models
                if source_name in sample_id_map:
                    sample = sample_id_map[source_name]
                else:
                    sample_id_map[source_name] = sample_id
                    sample = sample_id
                    sample_id += 1

            labels = file2label[raw]
            labels_str = ",".join(labels)
            label_set = set(labels)
            if "label free sample" in labels:
                label = "1"
            elif "TMT" in labels_str:
                choice = TMT_PLEXES[infer_tmtplex(label_set)]
                label = str(choice[labels[label_index[raw]]])
                label_index[raw] = label_index[raw] + 1
            elif "SILAC" in labels_str:
                if len(label_set) == 3:
                    label = str(self.silac3[labels[label_index[raw]].lower()])
                else:
                    label = str(self.silac2[labels[label_index[raw]].lower()])
            elif "ITRAQ" in labels_str:
                if (
                    len(label_set) > 4
                    or "ITRAQ113" in label_set
                    or "ITRAQ118" in label_set
                    or "ITRAQ119" in label_set
                    or "ITRAQ121" in label_set
                ):
                    label = str(self.itraq8plex[labels[label_index[raw]].lower()])
                else:
                    label = str(self.itraq4plex[labels[label_index[raw]].lower()])
                label_index[raw] = label_index[raw] + 1
            else:
                raise ValueError("Label " + str(row["comment[label]"]) + " is not recognized")

            out = get_openms_file_name(raw, extension_convert)

            f += tsv_line(str(Fraction_group[raw]), file2fraction[raw], out, label, str(sample))

        # sample table
        f += "\n"
        if "tmt" in ",".join(map(str.lower, file2label[sdrf["comment[data file]"].iloc[0]])) or "itraq" in ",".join(
            map(str.lower, file2label[sdrf["comment[data file]"].iloc[0]])
        ):
            openms_sample_header = ["Sample", "MSstats_Condition", "MSstats_BioReplicate", "MSstats_Mixture"]
        else:
            openms_sample_header = [
                "Sample",
                "MSstats_Condition",
                "MSstats_BioReplicate",
            ]
        f += "\t".join(openms_sample_header) + "\n"
        sample_row_written = []
        mixture_identifier = 1
        mixture_raw_tag = {}
        mixture_sample_tag = {}
        BioReplicate = []

        for _, row in sdrf.iterrows():
            raw = row["comment[data file]"]
            source_name = row["source name"]
            if re.search(sample_identifier_re, source_name) is not None:
                sample = re.search(sample_identifier_re, source_name).group(1)

                # MSstats BioReplicate column needs to be different for samples from different conditions.
                # so we can't just use the technical replicate identifier in sdrf but use the sample identifer
                MSstatsBioReplicate = sample
                if sample not in BioReplicate:
                    BioReplicate.append(sample)
            else:
                warning_message = "No sample identifier"
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1

                # Solve non-sample id expression models
                sample = sample_id_map[source_name]

                if sample not in BioReplicate:
                    BioReplicate.append(sample)
                MSstatsBioReplicate = str(BioReplicate.index(sample) + 1)
            if file2combined_factors[raw + row["comment[label]"]] is None:
                # no factor defined use sample as condition
                condition = source_name
            else:
                condition = file2combined_factors[raw + row["comment[label]"]]
            if len(openms_sample_header) == 4:
                if raw not in mixture_raw_tag:
                    if sample not in mixture_sample_tag:
                        mixture_raw_tag[raw] = mixture_identifier
                        mixture_sample_tag[sample] = mixture_identifier
                        mix_id = mixture_identifier
                        mixture_identifier += 1

                    else:
                        mix_id = mixture_sample_tag[sample]
                        mixture_raw_tag[raw] = mix_id
                else:
                    mix_id = mixture_raw_tag[raw]

                if sample not in sample_row_written:
                    f += str(sample) + "\t" + condition + "\t" + MSstatsBioReplicate + "\t" + str(mix_id) + "\n"
                    sample_row_written.append(sample)
            else:
                if sample not in sample_row_written:
                    f += str(sample) + "\t" + condition + "\t" + MSstatsBioReplicate + "\n"
                    sample_row_written.append(sample)

        with open(output_filename, "w+", encoding="utf-8") as of:
            of.write(f)

    def writeOneTableExperimentalDesign(
        self,
        output_filename,
        legacy,
        sdrf,
        file2technical_rep,
        source_name_list,
        source_name2n_reps,
        file2combined_factors,
        file2label,
        extension_convert,
        file2fraction,
    ):
        experimental_design = ""
        cdf = file2label[sdrf["comment[data file]"].iloc[0]].lower()
        if "tmt" in cdf or "itraq" in cdf:
            if legacy:
                open_ms_experimental_design_header = [
                    "Fraction_Group",
                    "Fraction",
                    "Spectra_Filepath",
                    "Label",
                    "Sample",
                    "MSstats_Condition",
                    "MSstats_BioReplicate",
                    "MSstats_Mixture",
                ]
            else:
                open_ms_experimental_design_header = [
                    "Fraction_Group",
                    "Fraction",
                    "Spectra_Filepath",
                    "Label",
                    "MSstats_Condition",
                    "MSstats_BioReplicate",
                    "MSstats_Mixture",
                ]
        else:
            if legacy:
                open_ms_experimental_design_header = [
                    "Fraction_Group",
                    "Fraction",
                    "Spectra_Filepath",
                    "Label",
                    "Sample",
                    "MSstats_Condition",
                    "MSstats_BioReplicate",
                ]
            else:
                open_ms_experimental_design_header = [
                    "Fraction_Group",
                    "Fraction",
                    "Spectra_Filepath",
                    "Label",
                    "MSstats_Condition",
                    "MSstats_BioReplicate",
                ]

        experimental_design += tsv_line(*open_ms_experimental_design_header)
        label_index = dict(zip(sdrf["comment[data file]"], [0] * len(sdrf["comment[data file]"])))
        sample_identifier_re = re.compile(r"sample (\d+)$", re.IGNORECASE)
        Fraction_group = {}
        mixture_identifier = 1
        mixture_raw_tag = {}
        mixture_sample_tag = {}
        BioReplicate = []
        sample_id_map = {}
        sample_id = 1
        pre_frac_group = 1
        raw_frac = {}
        for _, row in sdrf.iterrows():
            raw = row["comment[data file]"]
            source_name = row["source name"]
            replicate = file2technical_rep[raw]

            # calculate fraction group by counting all technical replicates of the preceeding source names
            source_name_index = source_name_list.index(source_name)
            offset = 0
            for i in range(source_name_index):
                offset = offset + int(source_name2n_reps[source_name_list[i]])

            fraction_group = offset + int(replicate)

            if fraction_group not in raw_frac:
                raw_frac[fraction_group] = [raw]

                if raw in Fraction_group:
                    if fraction_group < Fraction_group[raw]:
                        Fraction_group[raw] = fraction_group
                else:
                    Fraction_group[raw] = fraction_group

                # make fraction group consecutive
                if Fraction_group[raw] > pre_frac_group + 1:
                    Fraction_group[raw] = pre_frac_group + 1
                pre_frac_group = Fraction_group[raw]

            else:
                raw_frac[fraction_group].append(raw)
                Fraction_group[raw] = Fraction_group[raw_frac[fraction_group][0]]

            if re.search(sample_identifier_re, source_name) is not None:
                sample = re.search(sample_identifier_re, source_name).group(1)

                # MSstats BioReplicate column needs to be different for samples from different conditions.
                # so we can't just use the technical replicate identifier in sdrf but use the sample identifer
                MSstatsBioReplicate = sample
                if sample not in BioReplicate:
                    BioReplicate.append(sample)
            else:
                warning_message = "No sample number identifier"
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1

                # Solve non-sample id expression models
                if source_name in sample_id_map:
                    sample = sample_id_map[source_name]
                else:
                    sample_id_map[source_name] = sample_id
                    sample = sample_id
                    sample_id += 1
                if sample not in BioReplicate:
                    BioReplicate.append(sample)
                MSstatsBioReplicate = str(BioReplicate.index(sample) + 1)

            if file2combined_factors[raw + row["comment[label]"]] is None:
                # no factor defined -> use sample as condition
                condition = source_name
            else:
                condition = file2combined_factors[raw + row["comment[label]"]]

            # convert sdrf's label to openms's label
            labels = file2label[raw]
            labels_str = ",".join(labels)
            label_set = set(labels)
            if "label free sample" in labels:
                label = "1"

            elif "TMT" in labels_str:
                choice = TMT_PLEXES[infer_tmtplex(label_set)]
                label = str(choice[labels[label_index[raw]]])

                #  This can be avoided the dicts are built based on file&label as key.
                label_index[raw] = label_index[raw] + 1
            elif "SILAC" in labels_str:
                if len(label_set) == 3:
                    label = str(self.silac3[labels[label_index[raw]].lower()])
                else:
                    label = str(self.silac2[labels[label_index[raw]].lower()])
                label_index[raw] = label_index[raw] + 1
            elif "ITRAQ" in labels_str:
                if (
                    len(label_set) > 4
                    or "ITRAQ113" in label_set
                    or "ITRAQ118" in label_set
                    or "ITRAQ119" in label_set
                    or "ITRAQ121" in label_set
                ):
                    label = str(self.itraq8plex[labels[label_index[raw]].lower()])
                else:
                    label = str(self.itraq4plex[labels[label_index[raw]].lower()])
                label_index[raw] = label_index[raw] + 1
            else:
                raise ValueError("Label " + str(labels) + " is not recognized")

            out = get_openms_file_name(raw, extension_convert)

            if "MSstats_Mixture" in open_ms_experimental_design_header:
                if raw not in mixture_raw_tag:
                    if sample not in mixture_sample_tag:
                        mixture_raw_tag[raw] = mixture_identifier
                        mixture_sample_tag[sample] = mixture_identifier
                        mix_id = mixture_identifier
                        mixture_identifier += 1
                    else:
                        mix_id = mixture_sample_tag[sample]
                        mixture_raw_tag[raw] = mix_id
                else:
                    mix_id = mixture_raw_tag[raw]

                if legacy:
                    experimental_design += tsv_line(
                        str(Fraction_group[raw]),
                        file2fraction[raw],
                        out,
                        label,
                        str(sample),
                        condition,
                        MSstatsBioReplicate,
                        str(mix_id),
                    )
                else:
                    experimental_design += tsv_line(
                        str(Fraction_group[raw]),
                        file2fraction[raw],
                        out,
                        label,
                        condition,
                        MSstatsBioReplicate,
                        str(mix_id),
                    )
            else:
                if legacy:
                    experimental_design += tsv_line(
                        str(Fraction_group[raw]),
                        file2fraction[raw],
                        out,
                        label,
                        str(sample),
                        condition,
                        MSstatsBioReplicate,
                    )
                else:
                    experimental_design += tsv_line(
                        str(Fraction_group[raw]), file2fraction[raw], out, label, condition, MSstatsBioReplicate
                    )

        with open(output_filename, "w+", encoding="utf-8") as f:
            f.write(experimental_design)

    def save_search_settings_to_file(self, output_filename, sdrf, f2c):
        search_settings = ""
        open_ms_search_settings_header = [
            "URI",
            "Filename",
            "FixedModifications",
            "VariableModifications",
            "Proteomics Data Acquisition Method",
            "Label",
            "PrecursorMassTolerance",
            "PrecursorMassToleranceUnit",
            "FragmentMassTolerance",
            "FragmentMassToleranceUnit",
            "DissociationMethod",
            "Enzyme",
        ]
        search_settings += tsv_line(*open_ms_search_settings_header)
        raws = []
        TMT_mod = {
            "tmt6plex": ["TMT6plex (K)", "TMT6plex (N-term)"],
            "tmt10plex": ["TMT6plex (K)", "TMT6plex (N-term)"],
            "tmt11plex": ["TMT6plex (K)", "TMT6plex (N-term)"],
            "tmt16plex": ["TMTpro (K)", "TMTpro (N-term)"],
            "tmt18plex": [
                "TMTpro (K)",
                "TMTpro (N-term)",
            ],  # https://www.unimod.org/modifications_view.php?editid1=2016
        }
        ITRAQ_mod = {
            "itraq4plex": ["iTRAQ4plex (K)", "iTRAQ4plex (N-term)"],
            "itraq8plex": ["iTRAQ8plex (K)", "iTRAQ8plex (N-term)"],
        }
        for _, row in sdrf.iterrows():
            URI = row["comment[file uri]"]
            raw = row["comment[data file]"]
            if "comment[proteomics data acquisition method]" not in row:
                warning_message = (
                    "The comment[proteomics data acquisition method] column is missing, "
                    "default Data-Dependent Acquisition"
                )
                self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                acquisition_method = "Data-Dependent Acquisition"
            else:
                acquisition_method = row["comment[proteomics data acquisition method]"]
                if len(acquisition_method.split(";")) > 1:
                    acquisition_method = acquisition_method.split(";")[0].split("=")[1]

            if raw in raws:
                continue
            raws.append(raw)
            labels = f2c.file2label[raw]
            labels_str = ",".join(labels)
            label_set = set(labels)
            if "TMT" in labels_str:
                label = infer_tmtplex(label_set)
                # add default TMT modification when sdrf with label not contains TMT modification
                if "TMT" not in f2c.file2mods[raw][0] and "TMT" not in f2c.file2mods[raw][1]:
                    warning_message = (
                        "The sdrf with TMT label doesn't contain TMT modification. Adding default "
                        "variable modifications."
                    )
                    self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                    tmt_var_mod = TMT_mod[label]
                    if f2c.file2mods[raw][1]:
                        VarMod = ",".join(f2c.file2mods[raw][1].split(",") + tmt_var_mod)
                        f2c.file2mods[raw] = (f2c.file2mods[raw][0], VarMod)
                    else:
                        f2c.file2mods[raw] = (f2c.file2mods[raw][0], ",".join(tmt_var_mod))
            elif "label free sample" in label_set:
                label = "label free sample"
            elif "silac" in labels_str:
                label = "SILAC"
            elif "ITRAQ" in labels_str:
                if (
                    len(label_set) > 4
                    or "ITRAQ113" in label_set
                    or "ITRAQ118" in label_set
                    or "ITRAQ119" in label_set
                    or "ITRAQ121" in label_set
                ):
                    label = "itraq8plex"
                else:
                    label = "itraq4plex"
                # add default ITRAQ modification when sdrf with label not contains ITRAQ modification
                if "ITRAQ" not in f2c.file2mods[raw][0] and "ITRAQ" not in f2c.file2mods[raw][1]:
                    warning_message = (
                        "The sdrf with ITRAQ label doesn't contain label modification. Adding default "
                        "variable modifications."
                    )
                    self.warnings[warning_message] = self.warnings.get(warning_message, 0) + 1
                    itraq_var_mod = ITRAQ_mod[label]
                    if f2c.file2mods[raw][1]:
                        VarMod = ",".join(f2c.file2mods[raw][1].split(",") + itraq_var_mod)
                        f2c.file2mods[raw] = (f2c.file2mods[raw][0], VarMod)
                    else:
                        f2c.file2mods[raw] = (
                            f2c.file2mods[raw][0],
                            ",".join(itraq_var_mod),
                        )

            else:
                raise ValueError(
                    "Failed to find any supported labels. Supported labels are 'silac', 'label free "
                    f"sample', 'ITRAQ', and tmt labels in the format 'TMT131C', found {labels}"
                )

            # Why is the file name modified on the experimental design but not in the openms.tsv?
            # out_fname = get_openms_file_name(raw, extension_convert=extension_convert)
            out_fname = raw

            search_settings += tsv_line(
                URI,
                out_fname,
                f2c.file2mods[raw][0],
                f2c.file2mods[raw][1],
                acquisition_method,
                label,
                f2c.file2pctol[raw],
                f2c.file2pctolunit[raw],
                f2c.file2fragtol[raw],
                f2c.file2fragtolunit[raw],
                f2c.file2diss[raw],
                f2c.file2enzyme[raw],
            )
        # openms.tsv
        with open(output_filename, "w+", encoding="utf-8") as f:
            f.write(search_settings)
