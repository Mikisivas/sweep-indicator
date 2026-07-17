const { ValidationError } = require("./errors");

/**
 * Course — represents a single course record.
 *
 * All string processing (trimming, casing, validation) for a course
 * lives here, so every Course object in the system is guaranteed clean.
 */
class Course {
  /**
   * @param {string} code  e.g. "COS 201" or "cos201" — normalised to "COS201"
   * @param {string} title e.g. "  programming i " — normalised to "Programming I"
   * @param {number|string} unit credit units, an integer from 1 to 10
   */
  constructor(code, title, unit) {
    this.code = Course.normaliseCode(code);
    this.title = Course.normaliseTitle(title);
    this.unit = Course.validateUnit(unit);
  }

  /** Trim, remove inner spaces, uppercase, and validate the course code. */
  static normaliseCode(rawCode) {
    if (typeof rawCode !== "string" || rawCode.trim() === "") {
      throw new ValidationError("Course code cannot be empty.");
    }
    const code = rawCode.trim().replace(/\s+/g, "").toUpperCase();
    // Expect letters followed by digits, e.g. COS201, MTH102, GST111
    if (!/^[A-Z]{2,4}\d{3}$/.test(code)) {
      throw new ValidationError(
        `Invalid course code "${rawCode.trim()}". Expected 2-4 letters followed by 3 digits (e.g. COS201).`
      );
    }
    return code;
  }

  /** Trim and Title-Case the course title, keeping roman numerals uppercase. */
  static normaliseTitle(rawTitle) {
    if (typeof rawTitle !== "string" || rawTitle.trim() === "") {
      throw new ValidationError("Course title cannot be empty.");
    }
    const title = rawTitle
      .trim()
      .replace(/\s+/g, " ")
      .toLowerCase()
      .split(" ")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .map((word) => (/^(i|ii|iii|iv|v|vi)$/i.test(word) ? word.toUpperCase() : word))
      .join(" ");
    if (title.length < 3) {
      throw new ValidationError("Course title is too short (minimum 3 characters).");
    }
    return title;
  }

  /** Ensure the unit is a whole number between 1 and 10. */
  static validateUnit(rawUnit) {
    const unit = Number(String(rawUnit).trim());
    if (!Number.isInteger(unit) || unit < 1 || unit > 10) {
      throw new ValidationError(
        `Invalid unit "${String(rawUnit).trim()}". Unit must be a whole number between 1 and 10.`
      );
    }
    return unit;
  }

  /** One formatted table row for display. */
  toRow() {
    return `| ${this.code.padEnd(8)} | ${this.title.padEnd(35)} | ${String(this.unit).padStart(4)} |`;
  }

  /** Plain object for JSON serialisation when saving to file. */
  toJSON() {
    return { code: this.code, title: this.title, unit: this.unit };
  }
}

module.exports = Course;
