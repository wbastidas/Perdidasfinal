# R8 en modo release. El APK viaja por la red de la distribuidora y se instala en
# equipos con poco espacio libre, así que se reduce todo lo que se pueda.

# MapLibre usa JNI: sus clases nativas no pueden renombrarse ni eliminarse.
-keep class org.maplibre.android.** { *; }
-keep class org.maplibre.geojson.** { *; }
-dontwarn org.maplibre.**

# WorkManager instancia el worker por reflexión desde el nombre de la clase.
-keep class ec.cnel.ptnt.field.work.** { *; }

# Los modelos se serializan a JSON por nombre de campo: ofuscarlos rompería el
# contrato con el backend en silencio, que es la peor forma de romperlo.
-keep class ec.cnel.ptnt.field.data.** { *; }

# Trazas útiles en los informes de fallo del campo.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
