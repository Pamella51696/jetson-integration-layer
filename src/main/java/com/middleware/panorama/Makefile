
OPENCV_ROOT := ../../../../../../opencv
OPENCV_JAR := $(OPENCV_ROOT)/opencv-4.9.0-0.jar
#OPENCV_LIB := $(OPENCV_ROOT)/build/java/x64

ifeq ($(USER),mh96160)
ifeq ($(CYGWIN_ENV),1)
else
PARM := --enable-native-access=ALL-UNNAMED
endif
endif

ifeq ($(OS),Windows_NT)
CP := .;$(OPENCV_JAR)
else
CP := .:$(OPENCV_JAR)
endif

all:
	javac -cp "$(CP)" *.java

%.class : %.java
	@echo "$(CP)"
	javac -cp "$(CP)" $<

all_old : CameraStreamingServer.class CommonHandlers.class DistributedStitchingServer.class MetaBuilder.class PlayerPage.class StitchingCore.class VideoStreamingClient.class VideoStreamingServer.class

clean :
	rm -f -v *.class

runserver : VideoStreamingServer.class
	java --enable-native-access=ALL-UNNAMED -cp "$(CP)" VideoStreamingServer
	#java --enable-native-access=ALL-UNNAMED -Djava.library.path="$(OPENCV_LIB)" -cp "$(CP)" VideoStreamingServer
	#java -Djava.library.path="$(OPENCV_LIB)" -cp "$(CP)" VideoStreamingServer sample.mp4 8080
	#java $(PARM) -Djava.library.path="$(OPENCV_LIB)" -cp "$(CP)" VideoStreamingServer

runclient : VideoStreamingClient.class
	java -cp "$(CP)" VideoStreamingClient localhost 8080

