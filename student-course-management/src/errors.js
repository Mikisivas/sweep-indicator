/**
 * Custom exception types used across the application.
 * Throwing and catching these demonstrates structured error handling.
 */

class ValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "ValidationError";
  }
}

class DuplicateCourseError extends Error {
  constructor(code) {
    super(`A course with code "${code}" already exists.`);
    this.name = "DuplicateCourseError";
  }
}

class CourseNotFoundError extends Error {
  constructor(code) {
    super(`No course found with code "${code}".`);
    this.name = "CourseNotFoundError";
  }
}

class FileOperationError extends Error {
  constructor(message) {
    super(message);
    this.name = "FileOperationError";
  }
}

module.exports = {
  ValidationError,
  DuplicateCourseError,
  CourseNotFoundError,
  FileOperationError,
};
