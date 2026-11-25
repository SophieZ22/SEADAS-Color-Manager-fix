import java.nio.file.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
public final class PatchInitializationOrder implements Opcodes {
 public static void main(String[] args)throws Exception{
  ClassReader reader=new ClassReader(Files.readAllBytes(Path.of(args[0])));
  ClassNode c=new ClassNode(ASM9);reader.accept(c,0);
  if(!c.name.equals("org/esa/snap/rcp/colormanip/ColorManipulationFormImpl"))throw new IllegalStateException("Unexpected class");
  MethodNode m=c.methods.stream().filter(x->x.name.equals("installChildForm")&&x.desc.equals("()V")).findFirst().orElseThrow();
  MethodInsnNode install=null,shown=null;
  for(AbstractInsnNode a:m.instructions.toArray()){
   if(a instanceof MethodInsnNode n){
    if(n.name.equals("installMoreOptions")){if(install!=null)throw new IllegalStateException("Multiple installs");install=n;}
    if(n.name.equals("handleFormShown")){if(shown!=null)throw new IllegalStateException("Multiple shown handlers");shown=n;}
   }
  }
  if(install==null||shown==null||install.getPrevious().getOpcode()!=ALOAD||((VarInsnNode)install.getPrevious()).var!=0)throw new IllegalStateException("Unexpected instruction pattern");
  if(m.instructions.indexOf(install)>=m.instructions.indexOf(shown))throw new IllegalStateException("Options are already installed after channel initialization; do not apply this old patch blindly.");
  AbstractInsnNode load=install.getPrevious();m.instructions.remove(load);m.instructions.remove(install);
  InsnList deferred=new InsnList();deferred.add(load);deferred.add(install);m.instructions.insert(shown,deferred);
  ClassWriter writer=new ClassWriter(reader,ClassWriter.COMPUTE_MAXS);c.accept(writer);
  Files.write(Path.of(args[1]),writer.toByteArray());
  System.out.println("Moved installMoreOptions AFTER handleFormShown; no field/method/schema change.");
 }
}