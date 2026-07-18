import com.cburch.logisim.circuit.Circuit;
import com.cburch.logisim.circuit.CircuitState;
import com.cburch.logisim.circuit.Propagator;
import com.cburch.logisim.comp.Component;
import com.cburch.logisim.data.Value;
import com.cburch.logisim.file.Loader;
import com.cburch.logisim.file.LogisimFile;
import com.cburch.logisim.instance.StdAttr;
import com.cburch.logisim.proj.Project;
import com.cburch.logisim.std.wiring.Pin;

import java.io.File;
import java.nio.file.Files;
import java.util.*;

/**
 * Headless test-bench driver for a Logisim .circ file.
 * Usage: java TableDriver circuit.circ vectors.txt
 * Vector file format:
 *   inputs: NAME1 NAME2 ...
 *   outputs: NAME1 NAME2 ...
 *   then one line per step: bits for each input (e.g. "0 1 0 0 1 0"),
 *   optionally followed by "# comment". Blank lines / lines starting with # skipped.
 * After each step the driver sets the pins, propagates to quiescence, and
 * prints: step, input bits, output bits.
 */
public class TableDriver {
    public static void main(String[] args) throws Exception {
        Loader loader = new Loader(null);
        LogisimFile file = loader.openLogisimFile(new File(args[0]));
        Project proj = new Project(file);
        Circuit circ = file.getMainCircuit();
        CircuitState cs = proj.getCircuitState(circ);
        Propagator prop = cs.getPropagator();

        Map<String, Component> inPins = new HashMap<>();
        Map<String, Component> outPins = new HashMap<>();
        for (Component c : circ.getNonWires()) {
            if (c.getFactory() instanceof Pin) {
                Pin pf = (Pin) c.getFactory();
                String label = c.getAttributeSet().getValue(StdAttr.LABEL);
                boolean isInput = pf.isInputPin(cs.getInstanceState(c).getInstance());
                (isInput ? inPins : outPins).put(label, c);
            }
        }

        List<String> lines = Files.readAllLines(new File(args[1]).toPath());
        String[] inNames = null, outNames = null;
        int step = 0;
        for (String raw : lines) {
            String line = raw.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            if (line.startsWith("inputs:")) {
                inNames = line.substring(7).trim().split("\\s+");
                for (String n : inNames) if (!inPins.containsKey(n)) { System.out.println("ERROR no input pin " + n + " (have " + inPins.keySet() + ")"); return; }
                System.out.println("STEP | " + String.join(" ", inNames) + " | outputs");
                continue;
            }
            if (line.startsWith("outputs:")) {
                outNames = line.substring(8).trim().split("\\s+");
                for (String n : outNames) if (!outPins.containsKey(n)) { System.out.println("ERROR no output pin " + n + " (have " + outPins.keySet() + ")"); return; }
                continue;
            }
            String data = line.split("#")[0].trim();
            String comment = line.contains("#") ? line.substring(line.indexOf('#')) : "";
            String[] bits = data.split("\\s+");
            for (int i = 0; i < inNames.length; i++) {
                Component c = inPins.get(inNames[i]);
                Value v = bits[i].equals("1") ? Value.TRUE : Value.FALSE;
                Pin.FACTORY.setValue(cs.getInstanceState(c), v);
                cs.markComponentAsDirty(c);
            }
            prop.propagate();
            if (prop.isOscillating()) { System.out.println("OSCILLATION at step " + step); return; }
            StringBuilder sb = new StringBuilder();
            sb.append(String.format("%4d | %s | ", step, data));
            for (String n : outNames) {
                Value v = Pin.FACTORY.getValue(cs.getInstanceState(outPins.get(n)));
                sb.append(v.isFullyDefined() ? (v.toIntValue() == 1 ? "1" : "0") : "?");
                sb.append(" ");
            }
            sb.append(comment.isEmpty() ? "" : " " + comment);
            System.out.println(sb.toString());
            step++;
        }
        System.exit(0);
    }
}
