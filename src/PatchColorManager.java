import java.nio.file.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;

/** Local repair for unbinding an already disposed RGB view. */
public final class PatchColorManager implements Opcodes {
    static final String OWNER="org/esa/snap/rcp/colormanip/ColorManipulationFormImpl";
    static final String VIEW="org/esa/snap/ui/product/ProductSceneView";
    static final String IMAGE="org/esa/snap/ui/product/ProductSceneImage";
    static final String RASTER="org/esa/snap/core/datamodel/RasterDataNode";
    static final String PRODUCT="org/esa/snap/core/datamodel/Product";
    static final String GUARD="localFix_liveProductOrNull";
    public static void main(String[] args) throws Exception {
        byte[] original=Files.readAllBytes(Path.of(args[0]));
        ClassReader reader=new ClassReader(original);
        ClassNode node=new ClassNode(ASM9);reader.accept(node,0);
        if(!node.name.equals(OWNER))throw new IllegalArgumentException("Unexpected class");
        if(node.methods.stream().anyMatch(m->m.name.equals(GUARD)))throw new IllegalStateException("Already patched");
        MethodNode target=node.methods.stream().filter(m->m.name.equals("setProductSceneView")&&m.desc.equals("(L"+VIEW+";)V")).findFirst().orElseThrow();
        int calls=0;
        for(AbstractInsnNode insn:target.instructions.toArray()){
            if(insn instanceof MethodInsnNode call && call.getOpcode()==INVOKEVIRTUAL && call.owner.equals(VIEW)&&call.name.equals("getProduct")&&call.desc.equals("()L"+PRODUCT+";")){
                calls++;
                if(calls==1){
                    if(!(call.getPrevious() instanceof VarInsnNode oldView) || oldView.getOpcode()!=ALOAD || oldView.var!=2) throw new IllegalStateException("Unsupported old-view cleanup layout; inspect this version before patching.");
                    // Only old-view cleanup is changed. New-view validation/binding stays intact.
                    call.setOpcode(INVOKESTATIC);call.owner=OWNER;call.name=GUARD;call.desc="(L"+VIEW+";)L"+PRODUCT+";";call.itf=false;
                }
            }
        }
        if(calls!=2)throw new IllegalStateException("Unexpected getProduct call count: "+calls);
        MethodNode helper=new MethodNode(ASM9,ACC_PRIVATE|ACC_STATIC,GUARD,"(L"+VIEW+";)L"+PRODUCT+";",null,null);
        MethodVisitor mv=helper;mv.visitCode();
        Label viewOk=new Label(),imageOk=new Label(),arrayOk=new Label(),lengthOk=new Label(),rasterOk=new Label();
        mv.visitVarInsn(ALOAD,0);mv.visitJumpInsn(IFNONNULL,viewOk);nullReturn(mv);
        mv.visitLabel(viewOk);mv.visitFrame(F_SAME,0,null,0,null);
        mv.visitVarInsn(ALOAD,0);mv.visitMethodInsn(INVOKEVIRTUAL,VIEW,"getSceneImage","()L"+IMAGE+";",false);mv.visitVarInsn(ASTORE,1);
        mv.visitVarInsn(ALOAD,1);mv.visitJumpInsn(IFNONNULL,imageOk);nullReturn(mv);
        mv.visitLabel(imageOk);mv.visitFrame(F_APPEND,1,new Object[]{IMAGE},0,null);
        mv.visitVarInsn(ALOAD,1);mv.visitMethodInsn(INVOKEVIRTUAL,IMAGE,"getRasters","()[L"+RASTER+";",false);mv.visitVarInsn(ASTORE,2);
        mv.visitVarInsn(ALOAD,2);mv.visitJumpInsn(IFNONNULL,arrayOk);nullReturn(mv);
        mv.visitLabel(arrayOk);mv.visitFrame(F_APPEND,1,new Object[]{"[L"+RASTER+";"},0,null);
        mv.visitVarInsn(ALOAD,2);mv.visitInsn(ARRAYLENGTH);mv.visitJumpInsn(IFNE,lengthOk);nullReturn(mv);
        mv.visitLabel(lengthOk);mv.visitFrame(F_SAME,0,null,0,null);
        mv.visitVarInsn(ALOAD,2);mv.visitInsn(ICONST_0);mv.visitInsn(AALOAD);mv.visitVarInsn(ASTORE,3);
        mv.visitVarInsn(ALOAD,3);mv.visitJumpInsn(IFNONNULL,rasterOk);nullReturn(mv);
        mv.visitLabel(rasterOk);mv.visitFrame(F_APPEND,1,new Object[]{RASTER},0,null);
        mv.visitVarInsn(ALOAD,3);mv.visitMethodInsn(INVOKEVIRTUAL,RASTER,"getProduct","()L"+PRODUCT+";",false);mv.visitInsn(ARETURN);
        mv.visitMaxs(0,0);mv.visitEnd();node.methods.add(helper);
        // Preserve the existing frames and all existing logic; replacement has identical stack types.
        ClassWriter writer=new ClassWriter(reader,ClassWriter.COMPUTE_MAXS);node.accept(writer);
        Files.createDirectories(Path.of(args[1]).getParent());Files.write(Path.of(args[1]),writer.toByteArray());
        System.out.println("PATCHED old-view getProduct call only; added explicit lifetime guard.");
    }
    static void nullReturn(MethodVisitor mv){mv.visitInsn(ACONST_NULL);mv.visitInsn(ARETURN);}
}