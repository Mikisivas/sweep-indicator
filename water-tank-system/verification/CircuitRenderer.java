import com.cburch.logisim.circuit.Circuit;
import com.cburch.logisim.circuit.CircuitState;
import com.cburch.logisim.comp.Component;
import com.cburch.logisim.comp.ComponentDrawContext;
import com.cburch.logisim.data.Bounds;
import com.cburch.logisim.file.Loader;
import com.cburch.logisim.file.LogisimFile;
import com.cburch.logisim.proj.Project;

import javax.imageio.ImageIO;
import java.awt.*;
import java.awt.image.BufferedImage;
import java.io.File;

/** Render a Logisim .circ main circuit to a PNG using Logisim's own painting API. */
public class CircuitRenderer {
    public static void main(String[] args) throws Exception {
        System.setProperty("java.awt.headless", "true");
        Loader loader = new Loader(null);
        LogisimFile file = loader.openLogisimFile(new File(args[0]));
        Project proj = new Project(file);
        System.err.println("CK1 loaded"); Circuit circ = file.getMainCircuit();
        CircuitState cs = proj.getCircuitState(circ);

        int pad = 30;
        // first pass: measure with a scratch graphics context
        BufferedImage scratch = new BufferedImage(10, 10, BufferedImage.TYPE_INT_ARGB);
        Graphics2D sg = scratch.createGraphics();
        System.err.println("CK2 measuring"); Bounds b = circ.getBounds(sg);
        sg.dispose();

        double scale = args.length > 2 ? Double.parseDouble(args[2]) : 2.0;
        int w = (int) ((b.getWidth() + 2 * pad) * scale);
        int h = (int) ((b.getHeight() + 2 * pad) * scale);
        BufferedImage img = new BufferedImage(w, h, BufferedImage.TYPE_INT_RGB);
        Graphics2D g = img.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        g.setColor(Color.WHITE);
        g.fillRect(0, 0, w, h);
        g.scale(scale, scale);
        g.translate(-b.getX() + pad, -b.getY() + pad);
        g.setColor(Color.BLACK);

        System.err.println("CK3 drawing"); ComponentDrawContext ctx = new ComponentDrawContext(new java.awt.Canvas(), circ, cs, g, g);
        // draw wires + components
        circ.draw(ctx, java.util.Collections.<Component>emptySet());
        for (Component c : circ.getNonWires()) {
            c.draw(ctx);
        }
        g.dispose();
        System.err.println("CK4 writing"); ImageIO.write(img, "png", new File(args[1]));
        System.out.println("wrote " + args[1] + " (" + w + "x" + h + ")"); System.exit(0);
    }
}
