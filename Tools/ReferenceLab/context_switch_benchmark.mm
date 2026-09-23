// SPDX-License-Identifier: GPL-2.0-only
// Developer-only CGL cost probe. No game engine, media, window or input actions.
#import <Foundation/Foundation.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl3.h>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>

using Clock = std::chrono::steady_clock;
static double seconds(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double>(b-a).count();
}
static void require(bool condition, const char *message) {
    if (!condition) { fprintf(stderr,"CGL benchmark: %s\n",message); exit(1); }
}

int main(int argc, const char **argv) { @autoreleasepool {
    require(argc==2,"Usage: context-switch-benchmark REPORT.json");
    CGLPixelFormatAttribute attributes[]={kCGLPFAOpenGLProfile,
        (CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core,kCGLPFAAccelerated,
        kCGLPFAColorSize,(CGLPixelFormatAttribute)32,(CGLPixelFormatAttribute)0};
    CGLPixelFormatObj format=nullptr; GLint count=0; CGLContextObj context=nullptr;
    require(CGLChoosePixelFormat(attributes,&format,&count)==kCGLNoError&&count,"Accelerated pixel format unavailable");
    require(CGLCreateContext(format,nullptr,&context)==kCGLNoError&&context,"Context creation failed");
    CGLDestroyPixelFormat(format);
    require(CGLGetCurrentContext()==nullptr,"Expected a fresh thread without another OpenGL context");
    require(CGLSetCurrentContext(context)==kCGLNoError,"Context attach failed");
    NSString *renderer=[NSString stringWithUTF8String:(const char*)glGetString(GL_RENDERER)];
    NSString *version=[NSString stringWithUTF8String:(const char*)glGetString(GL_VERSION)];
    GLuint fbo=0,color=0,depth=0;
    glGenFramebuffers(1,&fbo);glBindFramebuffer(GL_FRAMEBUFFER,fbo);
    glGenTextures(1,&color);glBindTexture(GL_TEXTURE_2D,color);
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,2048,2048,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
    glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,color,0);
    glGenRenderbuffers(1,&depth);glBindRenderbuffer(GL_RENDERBUFFER,depth);
    glRenderbufferStorage(GL_RENDERBUFFER,GL_DEPTH24_STENCIL8,2048,2048);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER,GL_DEPTH_STENCIL_ATTACHMENT,GL_RENDERBUFFER,depth);
    require(glCheckFramebufferStatus(GL_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE,"Framebuffer incomplete");
    glViewport(0,0,640,480);glReadBuffer(GL_COLOR_ATTACHMENT0);glPixelStorei(GL_PACK_ALIGNMENT,1);
    std::vector<uint8_t> pixels(640*480*4);
    NSMutableArray *results=[NSMutableArray array];
    auto run=[&](NSString *name,bool switching,bool render) {
        require(CGLSetCurrentContext(switching?nullptr:context)==kCGLNoError,"Case setup failed");
        uint64_t errors=0,iterations=0;
        auto operation=[&]() {
            CGLContextObj previous=CGLGetCurrentContext();
            if(switching && CGLSetCurrentContext(context)!=kCGLNoError)++errors;
            if(render) {
                glClearColor(0.25f,0.5f,0.75f,1.0f);
                glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT|GL_STENCIL_BUFFER_BIT);
                glReadPixels(0,0,640,480,GL_RGBA,GL_UNSIGNED_BYTE,pixels.data());
            } else {
                // Keep the retained-context baseline query observable.
                asm volatile("" : : "r"(previous) : "memory");
            }
            if(switching && CGLSetCurrentContext(previous)!=kCGLNoError)++errors;
        };
        for(int i=0;i<3;++i)operation();
        auto begin=Clock::now(),end=begin;
        // A short batch limits clock-query overhead without allowing a long
        // uninterrupted GPU test. The report records the actual duration.
        const unsigned batch=render?1:64;
        do {
            for(unsigned i=0;i<batch;++i)operation();
            iterations+=batch;end=Clock::now();
        } while(seconds(begin,end)<0.18);
        const double elapsed=seconds(begin,end);
        require(CGLSetCurrentContext(context)==kCGLNoError,"Case completion attach failed");
        GLenum error=glGetError();require(error==GL_NO_ERROR&&errors==0,"CGL/OpenGL operation failed");
        require(elapsed<1.0,"A case exceeded the one-second measurement limit");
        if(render)require(pixels[0]==64&&pixels[1]==128&&pixels[2]==191&&pixels[3]==255,"Readback clear color differed");
        [results addObject:@{@"case":name,@"iterations":@(iterations),@"wallSeconds":@(elapsed),
            @"nanosecondsPerIteration":@(elapsed*1e9/iterations),@"attachRestorePerIteration":@(switching),
            @"clearAndReadbackPerIteration":@(render),@"errors":@0}];
    };
    run(@"retained-context-query",false,false);
    run(@"attach-restore-null",true,false);
    run(@"retained-clear-readback",false,true);
    run(@"attach-restore-clear-readback",true,true);
    NSDictionary *report=@{@"passed":@YES,@"renderer":renderer?:@"unknown",@"openGLVersion":version?:@"unknown",
        @"cases":results,@"offscreen":@YES,@"windowsCreated":@0,@"framebufferWidth":@2048,@"framebufferHeight":@2048,
        @"readbackWidth":@640,@"readbackHeight":@480,@"gameEngineLinked":@NO,@"gameMediaRead":@NO,
        @"scope":@"Short single-process CGL microbenchmark under concurrent host activity. Clear/readback is a synthetic workload, not the original game renderer. Does not establish game throughput, presentation or audio quality."};
    NSError *error=nil;
    NSData *json=[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted|NSJSONWritingSortedKeys error:&error];
    require(json!=nil,"JSON serialization failed");
    require([json writeToFile:[NSString stringWithUTF8String:argv[1]] options:NSDataWritingAtomic error:&error],"Cannot write report");
    fwrite(json.bytes,1,json.length,stdout);putchar('\n');
    glDeleteRenderbuffers(1,&depth);glDeleteTextures(1,&color);glDeleteFramebuffers(1,&fbo);
    CGLSetCurrentContext(nullptr);CGLDestroyContext(context);
    return 0;
} }
