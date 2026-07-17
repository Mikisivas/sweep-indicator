const path = require("path");
const readline = require("readline");
const CourseManager = require("./CourseManager");
const { ValidationError, DuplicateCourseError, CourseNotFoundError, FileOperationError } = require("./errors");

const DATA_FILE = path.join(__dirname, "..", "data", "courses.json");
const manager = new CourseManager();

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

// Buffer incoming lines so input is never lost, whether the program is used
// interactively or fed from a piped script/file.
const pendingLines = [];
const waitingResolvers = [];
let inputClosed = false;

rl.on("line", (line) => {
  const resolver = waitingResolvers.shift();
  if (resolver) resolver(line);
  else pendingLines.push(line);
});

rl.on("close", () => {
  inputClosed = true;
  // If input ends while we are still waiting, treat it as "Exit Program".
  while (waitingResolvers.length > 0) waitingResolvers.shift()("7");
});

/** Promise-based prompt so we can use async/await. */
function ask(question) {
  process.stdout.write(question);
  if (pendingLines.length > 0) return Promise.resolve(pendingLines.shift());
  if (inputClosed) return Promise.resolve("7");
  return new Promise((resolve) => waitingResolvers.push(resolve));
}

function printHeader() {
  console.log("==============================================================");
  console.log("        STUDENT COURSE MANAGEMENT SYSTEM  (COS 201)");
  console.log("==============================================================");
}

function printMenu() {
  console.log("");
  console.log("--------------------- MAIN MENU ---------------------");
  console.log("  1. Add Course");
  console.log("  2. View All Courses");
  console.log("  3. Search Course by Code");
  console.log("  4. Compute Total Units");
  console.log("  5. Save to File");
  console.log("  6. Load from File");
  console.log("  7. Exit Program");
  console.log("-----------------------------------------------------");
}

function printCourseTable(courses) {
  const line = "+----------+-------------------------------------+------+";
  console.log(line);
  console.log("| CODE     | TITLE                               | UNIT |");
  console.log(line);
  for (const course of courses) {
    console.log(course.toRow());
  }
  console.log(line);
}

// ------------------------- menu actions -------------------------

async function addCourse() {
  console.log("\n>> Add a new course");
  const code = await ask("   Course code (e.g. COS201): ");
  const title = await ask("   Course title: ");
  const unit = await ask("   Credit units (1-10): ");
  const course = manager.addCourse(code, title, unit);
  console.log(`   [OK] Added ${course.code} - ${course.title} (${course.unit} units).`);
}

function viewAllCourses() {
  console.log("\n>> All recorded courses");
  if (manager.count === 0) {
    console.log("   (No courses recorded yet. Use option 1 to add one.)");
    return;
  }
  printCourseTable(manager.getAll());
  console.log(`   ${manager.count} course(s) recorded.`);
}

async function searchCourse() {
  console.log("\n>> Search course by code");
  const code = await ask("   Enter course code to search: ");
  const course = manager.requireByCode(code); // throws CourseNotFoundError if absent
  console.log("   [FOUND]");
  printCourseTable([course]);
}

function computeTotalUnits() {
  console.log("\n>> Total credit units");
  console.log(`   ${manager.count} course(s), total units: ${manager.totalUnits()}`);
}

function saveToFile() {
  console.log("\n>> Save to file");
  const saved = manager.saveToFile(DATA_FILE);
  console.log(`   [OK] Saved ${saved} course(s) to ${path.relative(process.cwd(), DATA_FILE)}`);
}

function loadFromFile() {
  console.log("\n>> Load from file");
  const loaded = manager.loadFromFile(DATA_FILE);
  console.log(`   [OK] Loaded ${loaded} course(s) from ${path.relative(process.cwd(), DATA_FILE)}`);
}

// ------------------------- main loop -------------------------

/**
 * RECURSIVE menu: after each action completes (or fails with a handled
 * error), mainMenu() calls itself to show the menu again. The recursion
 * ends only when the user chooses option 7.
 */
async function mainMenu() {
  printMenu();
  const choice = (await ask("Select an option (1-7): ")).trim();

  try {
    switch (choice) {
      case "1":
        await addCourse();
        break;
      case "2":
        viewAllCourses();
        break;
      case "3":
        await searchCourse();
        break;
      case "4":
        computeTotalUnits();
        break;
      case "5":
        saveToFile();
        break;
      case "6":
        loadFromFile();
        break;
      case "7":
        console.log("\nGoodbye! Your courses are safe. Run the program again anytime.");
        if (!inputClosed) rl.close();
        return; // base case: stop recursing
      default:
        console.log(`   [!] "${choice}" is not a valid option. Please choose 1-7.`);
    }
  } catch (err) {
    if (
      err instanceof ValidationError ||
      err instanceof DuplicateCourseError ||
      err instanceof CourseNotFoundError ||
      err instanceof FileOperationError
    ) {
      console.log(`   [${err.name}] ${err.message}`);
    } else {
      console.log(`   [Unexpected error] ${err.message}`);
    }
  }

  return mainMenu(); // recursive step: show the menu again
}

printHeader();
mainMenu();
