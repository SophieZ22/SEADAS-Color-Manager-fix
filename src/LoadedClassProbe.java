import java.lang.instrument.Instrumentation;
import java.nio.file.*;
public final class LoadedClassProbe {
 public static void agentmain(String output,Instrumentation instrumentation)throws Exception{
  StringBuilder s=new StringBuilder();
  for(Class<?> c:instrumentation.getAllLoadedClasses()){
   if(c.getName().equals("org.esa.snap.rcp.colormanip.ColorManipulationFormImpl")){
    s.append("class=").append(c.getName()).append("\nloader=").append(c.getClassLoader()).append("\nsource=").append(c.getProtectionDomain().getCodeSource()).append('\n');
    boolean helper=java.util.Arrays.stream(c.getDeclaredMethods()).anyMatch(m->m.getName().equals("localFix_liveProductOrNull"));
    s.append("patched_helper_loaded=").append(helper).append('\n');
   }
  }
  Files.writeString(Path.of(output),s.toString());
 }
}