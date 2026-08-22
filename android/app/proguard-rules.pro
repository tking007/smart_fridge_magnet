# Add project specific ProGuard rules here.
-keepattributes Signature
-keepattributes *Annotation*

# Keep data classes
-keep class org.smartfridgemagnet.data.** { *; }
-keep class org.smartfridgemagnet.ai.** { *; }
