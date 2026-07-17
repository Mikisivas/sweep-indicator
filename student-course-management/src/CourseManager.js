const fs = require("fs");
const path = require("path");
const Course = require("./Course");
const {
  DuplicateCourseError,
  CourseNotFoundError,
  FileOperationError,
} = require("./errors");

/**
 * CourseManager — owns the internal list of courses and every operation
 * on it (add, list, search, total units, save, load).
 */
class CourseManager {
  constructor() {
    /** @type {Course[]} internal storage structure */
    this.courses = [];
  }

  /** Add a new course; rejects duplicates by course code. */
  addCourse(code, title, unit) {
    const course = new Course(code, title, unit); // validates + normalises
    if (this.findByCode(course.code) !== null) {
      throw new DuplicateCourseError(course.code);
    }
    this.courses.push(course);
    return course;
  }

  /** All courses (copy, so callers cannot mutate internal state). */
  getAll() {
    return [...this.courses];
  }

  get count() {
    return this.courses.length;
  }

  /**
   * RECURSIVE search: walks the list one index at a time instead of using
   * a loop. Base cases: end of list (not found) or code match (found).
   * Returns the Course or null.
   */
  findByCode(rawCode, index = 0) {
    const code = String(rawCode).trim().replace(/\s+/g, "").toUpperCase();
    if (index >= this.courses.length) return null; // base case: exhausted
    if (this.courses[index].code === code) return this.courses[index]; // base case: found
    return this.findByCode(code, index + 1); // recursive step
  }

  /** Like findByCode but throws if the course does not exist. */
  requireByCode(rawCode) {
    const course = this.findByCode(rawCode);
    if (course === null) {
      throw new CourseNotFoundError(String(rawCode).trim().toUpperCase());
    }
    return course;
  }

  /** Total credit units, computed with iteration (a simple loop). */
  totalUnits() {
    let total = 0;
    for (const course of this.courses) {
      total += course.unit;
    }
    return total;
  }

  /** Save the current course list to a JSON file. */
  saveToFile(filePath) {
    try {
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, JSON.stringify(this.courses, null, 2), "utf8");
      return this.courses.length;
    } catch (err) {
      throw new FileOperationError(`Could not save to "${filePath}": ${err.message}`);
    }
  }

  /** Load courses from a JSON file, replacing the current list. */
  loadFromFile(filePath) {
    let raw;
    try {
      raw = fs.readFileSync(filePath, "utf8");
    } catch (err) {
      throw new FileOperationError(
        `Could not read "${filePath}": ${err.code === "ENOENT" ? "file does not exist" : err.message}`
      );
    }

    let records;
    try {
      records = JSON.parse(raw);
    } catch {
      throw new FileOperationError(`"${filePath}" is not valid JSON.`);
    }
    if (!Array.isArray(records)) {
      throw new FileOperationError(`"${filePath}" does not contain a course list.`);
    }

    // Re-validate every record through the Course constructor so a
    // hand-edited or corrupted file cannot inject bad data.
    const loaded = records.map((r) => new Course(r.code, r.title, r.unit));
    this.courses = loaded;
    return loaded.length;
  }
}

module.exports = CourseManager;
